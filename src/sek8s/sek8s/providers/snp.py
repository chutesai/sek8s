"""AMD SEV-SNP attestation report provider.

Unlike TDX there is no external quote-generation daemon: the PSP produces and
signs the report in response to a guest request, so this reads it straight from
the kernel. Two interfaces expose it, preferred in this order:

1. configfs-TSM (``/sys/kernel/config/tsm/report``) — the generic interface,
   present on 6.7+ kernels. Preferred because it needs no ioctl plumbing.
2. ``/dev/sev-guest`` SNP_GET_REPORT ioctl — the older SNP-specific path, kept
   as a fallback for kernels whose sev-guest driver registers no TSM provider.

Note that Ubuntu 24.04's GA 6.8 kernel ships no ``sev-guest`` module at all;
guests must run the HWE kernel, which provides both interfaces.
"""

import asyncio
import ctypes
import fcntl
import os
import struct
import uuid

from loguru import logger

from sek8s.exceptions import SnpQuoteException
from sek8s.providers.base import REPORT_DATA_BYTES, QuoteProvider

CONFIGFS_TSM_REPORT = "/sys/kernel/config/tsm/report"
SEV_GUEST_DEVICE = "/dev/sev-guest"

# Attestation report as defined by the SEV Secure Nested Paging ABI.
SNP_REPORT_SIZE = 1184

# SNP_GET_REPORT = _IOWR('S', 0x0, struct snp_guest_request_ioctl)
SNP_GET_REPORT = 0xC0205300
# struct snp_report_req { __u8 user_data[64]; __u32 vmpl; __u8 rsvd[28]; }
SNP_REPORT_REQ_SIZE = 96
# struct snp_report_resp { __u8 data[4000]; } — the report follows a 32-byte header.
SNP_REPORT_RESP_SIZE = 4000
SNP_REPORT_RESP_HEADER = 32


class SnpQuoteProvider(QuoteProvider):
    """Async SEV-SNP report provider with cert hash binding."""

    tee_type = "snp"
    guest_device = SEV_GUEST_DEVICE
    quote_exception = SnpQuoteException

    async def get_quote(self, nonce: str) -> bytes:
        """Generate an SEV-SNP attestation report bound to ``nonce``.

        Args:
            nonce: hex string; combined with the server cert hash to fill the
                report's 64-byte ``report_data``.

        Returns:
            The raw 1184-byte attestation report.
        """
        report_data = self._report_data_bytes(nonce)
        try:
            report = await asyncio.to_thread(self._fetch_report, report_data)
        except SnpQuoteException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error generating SEV-SNP report: {e}")
            raise SnpQuoteException(f"Unexpected error generating SEV-SNP report: {e}")

        if len(report) < SNP_REPORT_SIZE:
            raise SnpQuoteException(
                f"SEV-SNP report too short: {len(report)} bytes "
                f"(expected {SNP_REPORT_SIZE})"
            )

        logger.info(f"Successfully generated SEV-SNP report ({len(report)} bytes).")
        return report[:SNP_REPORT_SIZE]

    def _fetch_report(self, report_data: bytes) -> bytes:
        """Read a report, preferring configfs-TSM and falling back to the ioctl."""
        if os.path.isdir(CONFIGFS_TSM_REPORT):
            try:
                return self._fetch_via_configfs(report_data)
            except Exception as e:
                logger.warning(
                    f"configfs-TSM report failed ({e}); falling back to {SEV_GUEST_DEVICE}"
                )
        return self._fetch_via_ioctl(report_data)

    def _fetch_via_configfs(self, report_data: bytes) -> bytes:
        """Read a report through the generic configfs-TSM interface."""
        entry = os.path.join(CONFIGFS_TSM_REPORT, f"sek8s-{uuid.uuid4().hex}")
        os.mkdir(entry)
        try:
            with open(os.path.join(entry, "inblob"), "wb") as f:
                f.write(report_data)
            with open(os.path.join(entry, "outblob"), "rb") as f:
                report = f.read()
            return report
        finally:
            # configfs entries persist until removed; a leaked directory would
            # accumulate on every attestation request.
            try:
                os.rmdir(entry)
            except OSError as e:  # pragma: no cover - cleanup best effort
                logger.warning(f"Failed to remove configfs-TSM entry {entry}: {e}")

    def _fetch_via_ioctl(self, report_data: bytes) -> bytes:
        """Read a report through the SNP-specific /dev/sev-guest ioctl."""
        if not os.path.exists(SEV_GUEST_DEVICE):
            raise SnpQuoteException(
                f"{SEV_GUEST_DEVICE} not present and configfs-TSM unavailable; "
                "the guest kernel exposes no SEV-SNP attestation interface."
            )

        req = bytearray(SNP_REPORT_REQ_SIZE)
        req[0:REPORT_DATA_BYTES] = report_data
        resp = bytearray(SNP_REPORT_RESP_SIZE)

        # Buffers must stay referenced for the lifetime of the ioctl: the struct
        # below carries their raw addresses.
        req_buf = (ctypes.c_char * SNP_REPORT_REQ_SIZE).from_buffer(req)
        resp_buf = (ctypes.c_char * SNP_REPORT_RESP_SIZE).from_buffer(resp)
        arg = bytearray(
            struct.pack(
                "=BxxxxxxxQQQ",
                1,  # msg_version
                ctypes.addressof(req_buf),
                ctypes.addressof(resp_buf),
                0,  # exitinfo2
            )
        )

        fd = os.open(SEV_GUEST_DEVICE, os.O_RDWR)
        try:
            fcntl.ioctl(fd, SNP_GET_REPORT, arg, True)
        except OSError as e:
            raise SnpQuoteException(f"SNP_GET_REPORT ioctl failed: {e}")
        finally:
            os.close(fd)

        status, report_size = struct.unpack_from("=II", resp, 0)
        if status != 0:
            raise SnpQuoteException(f"SNP_GET_REPORT returned status {status}")
        # Bounds hoisted to locals: black spaces out a slice whose bounds are expressions,
        # which flake8 then rejects as E203 (no longer ignored in .flake8).
        start = SNP_REPORT_RESP_HEADER
        end = start + report_size
        return bytes(resp[start:end])
