"""Re-export header constants from sek8s-common for backward compatibility."""

from sek8s_common.constants import (
    HOTKEY_HEADER,
    MINER_HEADER,
    NONCE_HEADER,
    NONCE_MAX_AGE_SECONDS,
    SIGNATURE_HEADER,
    VALIDATOR_HEADER,
)

__all__ = [
    "HOTKEY_HEADER",
    "MINER_HEADER",
    "NONCE_HEADER",
    "NONCE_MAX_AGE_SECONDS",
    "SIGNATURE_HEADER",
    "VALIDATOR_HEADER",
]
