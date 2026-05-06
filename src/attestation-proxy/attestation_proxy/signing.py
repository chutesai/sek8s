"""Response signing utilities for the attestation proxy.

Signs response bodies with the host TLS private key (RSA-PKCS1v15-SHA256) so
that clients can verify the proxy holds the private key corresponding to the
TDX-attested public certificate.

Security invariant: no key material (bytes, PEM content, repr) is ever logged.
Only the key file path and metadata (type, size) are logged.
"""

import base64
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from loguru import logger


def load_private_key(path: Optional[Path]) -> Optional[RSAPrivateKey]:
    """Load an RSA private key from a PEM file.

    Returns the key on success, or None if the path is absent, the file does
    not exist, or the contents cannot be parsed as an RSA private key.  Never
    logs key material.
    """
    if path is None:
        logger.warning("TLS key path is not configured; response signing disabled")
        return None

    try:
        key_bytes = Path(path).read_bytes()
        key = serialization.load_pem_private_key(key_bytes, password=None)
    except FileNotFoundError:
        logger.warning(
            f"TLS private key not found at {path}; response signing disabled"
        )
        return None
    except Exception as exc:
        logger.warning(
            f"Failed to load TLS private key from {path}: {exc}; response signing disabled"
        )
        return None

    if not isinstance(key, RSAPrivateKey):
        logger.warning(
            f"Key at {path} is not an RSA private key (got {type(key).__name__}); "
            "response signing disabled"
        )
        return None

    key_size = key.key_size
    logger.info(f"Loaded {key_size}-bit RSA private key from {path}")
    return key


def sign_response_body(key: RSAPrivateKey, body: bytes) -> str:
    """Sign *body* with *key* using RSA-PKCS1v15-SHA256.

    Returns the signature as a base64-encoded string suitable for use in an
    HTTP header value.
    """
    signature = key.sign(body, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")
