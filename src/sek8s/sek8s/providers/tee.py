"""TEE detection and quote-provider selection.

The guest image is built once and runs on both Intel TDX and AMD SEV-SNP hosts,
so which TEE it is running under is discovered at boot rather than baked in.
Detection keys off the guest device node each TEE's kernel driver creates,
which is unambiguous and needs no privileges.
"""

import os
from enum import Enum

from loguru import logger

from sek8s.exceptions import AttestationException
from sek8s.providers.base import QuoteProvider

TDX_GUEST_DEVICE = "/dev/tdx_guest"
SEV_GUEST_DEVICE = "/dev/sev-guest"
CONFIGFS_TSM_REPORT = "/sys/kernel/config/tsm/report"


class TeeType(str, Enum):
    """Confidential-computing platform the guest is running under."""

    TDX = "tdx"
    SNP = "snp"


def detect_tee(override: "TeeType | str | None" = None) -> TeeType:
    """Determine which TEE this guest is running under.

    Args:
        override: explicit type from configuration; skips detection when set.
            Useful on hosts whose kernel exposes neither device node.

    Raises:
        AttestationException: when no TEE can be determined. Deliberately fatal
            rather than defaulting — guessing wrong produces evidence a verifier
            silently rejects, which is far harder to diagnose than a clear error.
    """
    if override is not None:
        tee_type = TeeType(override)
        logger.info(f"TEE type overridden by configuration: {tee_type.value}")
        return tee_type

    if os.path.exists(TDX_GUEST_DEVICE):
        logger.info(f"Detected Intel TDX guest ({TDX_GUEST_DEVICE} present)")
        return TeeType.TDX

    if os.path.exists(SEV_GUEST_DEVICE):
        logger.info(f"Detected AMD SEV-SNP guest ({SEV_GUEST_DEVICE} present)")
        return TeeType.SNP

    raise AttestationException(
        "Could not determine TEE type: neither "
        f"{TDX_GUEST_DEVICE} nor {SEV_GUEST_DEVICE} is present. On SEV-SNP this "
        "usually means the guest booted a kernel without the sev-guest driver "
        "(Ubuntu 24.04's GA 6.8 kernel ships none — use the HWE kernel)."
    )


def get_quote_provider(override: "TeeType | str | None" = None) -> QuoteProvider:
    """Return the evidence provider for the detected (or overridden) TEE."""
    tee_type = detect_tee(override)

    # Imported lazily so a TDX guest never imports SNP-only modules and vice
    # versa; each provider pulls in interfaces the other platform lacks.
    if tee_type is TeeType.TDX:
        from sek8s.providers.tdx import TdxQuoteProvider

        return TdxQuoteProvider()

    from sek8s.providers.snp import SnpQuoteProvider

    return SnpQuoteProvider()
