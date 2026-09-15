"""Shared plumbing for TEE attestation-evidence providers.

Intel TDX and AMD SEV-SNP differ in how evidence is produced but agree on what
it must bind: the caller's nonce plus a hash of the per-VM proxy certificate.
Both carry that in a 64-byte report-data field, so the construction is identical
and lives here rather than being duplicated per provider.
"""

import hashlib
import subprocess  # nosec B404
from abc import ABC, abstractmethod

from loguru import logger

from sek8s.exceptions import AttestationException
from sek8s.nonce import QUOTE_NONCE_HEX_LEN, validate_quote_nonce

# The per-VM proxy cert setup_vm_tls generates in initramfs and the proxy serves; report data must
# hash this exact cert so the validator's expected_cert_hash matches.
SERVER_CERT = "/run/chutes/proxy-tls/server.crt"
# 64-byte report data as hex: the 64-char nonce followed by the 64-char cert hash.
REPORT_DATA_HEX_LEN = QUOTE_NONCE_HEX_LEN * 2
REPORT_DATA_BYTES = REPORT_DATA_HEX_LEN // 2


class QuoteProvider(ABC):
    """Base for per-TEE attestation-evidence providers."""

    #: Discriminator carried in AttestationResponse.tee_type.
    tee_type: str = ""

    #: Provider failures are raised as this; subclasses narrow it.
    quote_exception: type[AttestationException] = AttestationException

    def _get_cert_hash(self) -> str:
        """
        Compute SHA-256 hash of the server certificate's public key.
        This binds the quote to the specific certificate being used.

        Returns:
            64-character hex string (SHA-256 hash)
        """
        try:
            # Extract public key from certificate
            pubkey_result = subprocess.run(  # nosec B603 B607
                ["openssl", "x509", "-in", SERVER_CERT, "-pubkey", "-noout"],
                capture_output=True,
                check=True,
                text=True,
            )

            # Convert public key to DER format and hash it
            der_result = subprocess.run(  # nosec B603 B607
                ["openssl", "pkey", "-pubin", "-outform", "der"],
                input=pubkey_result.stdout.encode("utf-8"),
                capture_output=True,
                check=True,
                text=False,
            )

            # Compute SHA-256 hash
            cert_hash = hashlib.sha256(der_result.stdout).hexdigest()

            logger.debug(f"Computed cert hash: {cert_hash}")
            return cert_hash

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to compute cert hash: {e}")
            raise self.quote_exception(f"Failed to compute certificate hash: {e}")
        except Exception as e:
            logger.error(f"Unexpected error computing cert hash: {e}")
            raise self.quote_exception(
                f"Unexpected error computing certificate hash: {e}"
            )

    def _build_report_data(self, nonce: str) -> str:
        """Report data as hex: ``nonce || cert_hash``.

        Both halves are fixed-width, so this validates rather than truncates: an
        over-long nonce would displace cert_hash out of the field entirely and
        the hardware would sign evidence binding no certificate.
        """
        cert_hash = self._get_cert_hash()
        nonce = validate_quote_nonce(nonce)
        report_data = f"{nonce}{cert_hash}"

        if len(report_data) != REPORT_DATA_HEX_LEN:
            raise self.quote_exception(
                f"Report data must be exactly {REPORT_DATA_HEX_LEN} hex characters, "
                f"got {len(report_data)} (cert_hash was {len(cert_hash)})"
            )
        return report_data

    def _report_data_bytes(self, nonce: str) -> bytes:
        """Report data as exactly ``REPORT_DATA_BYTES`` raw bytes."""
        report_data = self._build_report_data(nonce)
        try:
            return bytes.fromhex(report_data)
        except ValueError as e:
            raise self.quote_exception(f"Report data is not valid hex: {e}")

    @abstractmethod
    async def get_quote(self, nonce: str) -> bytes:
        """Return raw attestation evidence bound to ``nonce``."""
        ...
