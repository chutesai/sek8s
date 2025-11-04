from functools import lru_cache
import time
from typing import Optional
from bittensor_wallet import Keypair
from fastapi import HTTPException, Header, Request, status
from loguru import logger
from substrateinterface import KeypairType
from sek8s.config import AttestationProxyConfig
from sek8s.services._shared import HOTKEY_HEADER, MINER_HEADER, NONCE_HEADER, NONCE_MAX_AGE_SECONDS, SIGNATURE_HEADER, VALIDATOR_HEADER

settings = AttestationProxyConfig()

@lru_cache(maxsize=2)
def get_keypair(ss58: str) -> Keypair:
    """Helper to load keypairs efficiently."""
    return Keypair(ss58_address=ss58, crypto_type=KeypairType.SR25519)

async def verify_validator_signature(
    request: Request,
    validator: Optional[str] = Header(None, alias=VALIDATOR_HEADER),
    nonce: Optional[str] = Header(None, alias=NONCE_HEADER),
    signature: Optional[str] = Header(None, alias=SIGNATURE_HEADER),
) -> bool:
    """
    Verify Bittensor validator signature - optional authentication.
    
    Uses public-key cryptography (SR25519):
    - Validator signs with their private key
    - Server verifies with public key (derived from validator's SS58 address)
    
    This ensures only authorized validators can access the external endpoints.
    """
    
    logger.info(f"Checking external request: {validator}:{nonce} - {signature}")

    # If some but not all headers provided, reject
    if not all([validator, nonce, signature]):
        logger.warning(
            f"Partial authentication headers provided: "
            f"validator={bool(validator)}, nonce={bool(nonce)}, signature={bool(signature)}"
        )
        raise HTTPException(
            status_code=401,
            detail="Incomplete authentication: requires X-Chutes-Validator, X-Chutes-Nonce, X-Chutes-Signature"
        )
    
    # Verify validator is allowed
    if validator not in settings.allowed_validators:
        logger.warning(f"Unauthorized validator attempted access: {validator}")
        raise HTTPException(
            status_code=403,
            detail="Validator not authorized"
        )
    
    # Verify nonce is recent (prevent replay attacks)
    try:
        nonce_timestamp = int(nonce)
        current_time = int(time.time())
        age = current_time - nonce_timestamp
        
        if age >= NONCE_MAX_AGE_SECONDS:
            logger.warning(
                f"Expired nonce from validator {validator}: "
                f"age={age}s, max={NONCE_MAX_AGE_SECONDS}s"
            )
            raise HTTPException(
                status_code=401,
                detail=f"Nonce expired (age: {age}s, max: {NONCE_MAX_AGE_SECONDS}s)"
            )
        
        if age < 0:
            logger.warning(f"Future nonce from validator {validator}: {nonce}")
            raise HTTPException(
                status_code=401,
                detail="Invalid nonce (future timestamp)"
            )
            
    except ValueError:
        logger.warning(f"Invalid nonce format from validator {validator}: {nonce}")
        raise HTTPException(
            status_code=401,
            detail="Invalid nonce format (must be Unix timestamp)"
        )
    
    # Build signature string: validator:nonce:payload_hash
    if hasattr(request.state, 'body_sha256') and request.state.body_sha256:
        payload_hash = request.state.body_sha256
    else:
        # For GET requests with no body, use the URL path as the purpose
        payload_hash = request.url.path
    
    # Format: validator:nonce:payload_hash
    signature_string = f"{validator}:{nonce}:{payload_hash}"
    
    # Verify signature using public-key cryptography
    # get_keypair creates a keypair from SS58 address (contains only public key)
    # The verify() method checks if signature was created by the matching private key
    try:
        keypair = get_keypair(validator)
        signature_bytes = bytes.fromhex(signature)
        
        if not keypair.verify(signature_string, signature_bytes):
            logger.warning(
                f"Invalid signature from validator {validator}: "
                f"signature_string='{signature_string}'"
            )
            raise HTTPException(
                status_code=401,
                detail="Invalid signature"
            )
        
        logger.info(f"Successfully authenticated validator {validator}")
        return True
        
    except ValueError as e:
        logger.error(f"Signature hex decode error for validator {validator}: {e}")
        raise HTTPException(
            status_code=401,
            detail="Invalid signature format (must be hex)"
        )
    except Exception as e:
        logger.error(f"Signature verification error for validator {validator}: {e}")
        raise HTTPException(
            status_code=401,
            detail="Signature verification failed"
        )
    
def authorize(allow_miner=False, allow_validator=False, purpose: Optional[str] = None):
    def _authorize(
        request: Request,
        hotkey: str | None = Header(None, alias=HOTKEY_HEADER),
        nonce: str | None = Header(None, alias=NONCE_HEADER),
        signature: str | None = Header(None, alias=SIGNATURE_HEADER),
    ):
        """
        Verify the authenticity of a request.
        """

        logger.info(f"Authorizing {request.url.path}: {hotkey=} {nonce=} {signature=} {purpose=} {request.state.body_sha256=}")

        allowed_signers = []
        if allow_miner:
            allowed_signers.append(settings.miner_ss58)
        if allow_validator:
            allowed_signers += settings.allowed_validators
        logger.info(f"{allowed_signers=}")
        if (
            any(not v for v in [hotkey, nonce, signature])
            or hotkey not in allowed_signers
            or int(time.time()) - int(nonce) >= 30
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="go away (missing)"
            )
        signature_string = ":".join(
            [
                hotkey,
                nonce,
                request.state.body_sha256 if request.state.body_sha256 else purpose,
            ]
        )
        logger.info(f"Signature string: {signature_string}")
        if not get_keypair(hotkey).verify(signature_string, bytes.fromhex(signature)):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"go away: (sig): {request.state.body_sha256=} {signature_string=}",
            )

    return _authorize