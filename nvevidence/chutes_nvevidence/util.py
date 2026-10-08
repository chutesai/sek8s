import re

from loguru import logger

from chutes_nvevidence.exceptions import NonceError


def validate_nonce(nonce: str) -> str:
    """
    Validate that nonce is a 64-character hex string (32 bytes).
    
    Args:
        nonce: Hex string, must be exactly 64 characters (0-9, a-f, A-F)
        
    Returns:
        64-character lowercase hex string (32 bytes)
        
    Raises:
        NonceError: If nonce is invalid
    """
    # Remove any whitespace
    nonce = nonce.strip()
    
    # Validate not empty
    if len(nonce) == 0:
        raise NonceError("Nonce cannot be empty")
    
    # Validate length is exactly 64 characters
    if len(nonce) != 64:
        raise NonceError(
            f"Nonce must be exactly 64 hex characters (32 bytes), got {len(nonce)} characters. "
            f"Nonce: {nonce}"
        )
    
    # Not bytes.fromhex: it skips embedded whitespace, so 64 characters could decode short.
    if not re.fullmatch(r"[0-9a-fA-F]+", nonce):
        raise NonceError("Nonce must contain only hexadecimal characters (0-9, a-f)")
    
    # Normalize to lowercase
    normalized_nonce = nonce.lower()
    
    logger.debug(f"Validated nonce: {normalized_nonce} (64 hex chars, 32 bytes)")
    
    return normalized_nonce