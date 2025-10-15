import hashlib
from loguru import logger

from chutes_nvevidence.exceptions import NonceError


def validate_and_format_nonce(nonce: str) -> str:
    """
    Validate and format nonce to be a 32-byte hex string (64 hex characters).
    
    Expects a plain string (not hex) of up to 32 characters.
    Converts to hex and pads to 64 hex characters (32 bytes).
    
    Args:
        nonce: Plain string, max 32 characters
        
    Returns:
        64-character hex string (32 bytes)
        
    Raises:
        ValueError: If nonce is invalid
    """
    # Remove any whitespace
    nonce = nonce.strip()
    
    # Validate length (max 32 characters before hex encoding)
    if len(nonce) > 32:
        raise NonceError(f"Nonce too long: {len(nonce)} characters (max 32). Nonce: {nonce}")
    
    if len(nonce) == 0:
        raise NonceError("Nonce cannot be empty")
    
    # Convert to hex and pad to 64 characters (32 bytes)
    hex_nonce = nonce.encode('utf-8').hex()
    padded_nonce = hex_nonce.ljust(64, '0')
    
    logger.debug(f"Original nonce: {nonce} ({len(nonce)} chars)")
    logger.debug(f"Hex encoded: {hex_nonce} ({len(hex_nonce)} chars)")
    logger.debug(f"Padded to 32 bytes: {padded_nonce}")
    
    return padded_nonce