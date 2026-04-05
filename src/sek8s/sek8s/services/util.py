"""Service utilities: signing and re-exported auth helpers."""

import hashlib
import json
import time
from typing import Any, Dict, Optional, Tuple

from loguru import logger
from sek8s_common.auth import authorize, get_keypair, verify_validator_signature
from sek8s_common.constants import (
    HOTKEY_HEADER,
    MINER_HEADER,
    NONCE_HEADER,
    SIGNATURE_HEADER,
    VALIDATOR_HEADER,
)

from sek8s.config import MinerConfig

__all__ = [
    "authorize",
    "get_keypair",
    "verify_validator_signature",
    "sign_request",
]


def _get_signing_message(
    hotkey: str,
    nonce: str,
    payload_str: str | bytes | None,
    purpose: str | None = None,
    payload_hash: str | None = None,
) -> str:
    if payload_str:
        if isinstance(payload_str, str):
            payload_str = payload_str.encode()
        return f"{hotkey}:{nonce}:{hashlib.sha256(payload_str).hexdigest()}"
    elif purpose:
        return f"{hotkey}:{nonce}:{purpose}"
    elif payload_hash:
        return f"{hotkey}:{nonce}:{payload_hash}"
    else:
        raise ValueError("Either payload_str or purpose must be provided")


def sign_request(
    payload: Dict[str, Any] | str | None = None,
    purpose: str | None = None,
    management: bool = False,
) -> Tuple[Dict[str, str], Optional[str]]:
    """
    Generate signed headers for miner requests to validators (e.g. GET /chutes/{id}/hf_info).
    Uses purpose for GET (no body); uses payload for POST. Returns (headers, payload_str or None).
    """
    miner_settings = MinerConfig()
    if miner_settings.miner_ss58 is None:
        raise ValueError("MINER_SS58 must be configured")
    if miner_settings.miner_keypair is None:
        raise ValueError("miner_keypair must be configured")
    nonce = str(int(time.time()))
    headers: Dict[str, str] = {
        HOTKEY_HEADER: miner_settings.miner_ss58,
        NONCE_HEADER: nonce,
    }
    signature_string: str | None = None
    payload_string: str | None = None
    if payload is not None:
        if isinstance(payload, (list, dict)):
            headers["Content-Type"] = "application/json"
            payload_string = json.dumps(payload)
        else:
            payload_string = payload
        signature_string = _get_signing_message(
            miner_settings.miner_ss58,
            nonce,
            payload_str=payload_string,
            purpose=None,
        )
    else:
        signature_string = _get_signing_message(
            miner_settings.miner_ss58, nonce, payload_str=None, purpose=purpose
        )
    if signature_string is None:
        raise ValueError("Failed to generate signature string")
    if management:
        signature_string = miner_settings.miner_ss58 + ":" + signature_string
        headers[MINER_HEADER] = headers.pop(HOTKEY_HEADER)
        headers[VALIDATOR_HEADER] = headers[MINER_HEADER]
    logger.debug(f"Signing message: {signature_string}")
    headers[SIGNATURE_HEADER] = miner_settings.miner_keypair.sign(
        signature_string.encode()
    ).hex()
    return (headers, payload_string)
