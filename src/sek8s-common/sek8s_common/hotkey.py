"""Miner hotkey keypair from the credential the guest is given.

The VM carries exactly one credential for the miner hotkey: the 64-byte sr25519 private key
(``MINER_PRIVATE_KEY``, from current Bittensor hotkey files) or the 32-byte seed (``MINER_SEED``).
The private key is preferred when both are set, as in chutes-miner's ``load_miner_keypair``.
"""

from typing import Optional

from substrateinterface import Keypair, KeypairType

# Generic Substrate prefix, as Bittensor uses; MINER_SS58 addresses start with '5'.
SS58_FORMAT = 42


def load_miner_keypair(
    ss58: Optional[str], private_key: Optional[str], seed: Optional[str]
) -> Optional[Keypair]:
    """Keypair from ``private_key`` when set, else ``seed``; None when neither is set.

    Raises ValueError when the key cannot be loaded or its address is not ``ss58``, so the VM
    never signs as a hotkey other than the one it claims. Messages never contain key material.
    """
    if private_key:
        source = "MINER_PRIVATE_KEY"
    elif seed:
        source = "MINER_SEED"
    else:
        return None
    if not ss58:
        raise ValueError(f"{source} is set but MINER_SS58 is not")

    try:
        if private_key:
            keypair = Keypair.create_from_private_key(
                private_key, ss58_format=SS58_FORMAT, crypto_type=KeypairType.SR25519
            )
        else:
            keypair = Keypair.create_from_seed(seed, ss58_format=SS58_FORMAT)
    # substrate-interface raises assorted exception types on malformed input.
    except Exception as exc:
        raise ValueError(
            f"{source} is not a valid sr25519 key ({type(exc).__name__})"
        ) from None

    if keypair.ss58_address != ss58:
        raise ValueError(
            f"Keypair from {source} has address {keypair.ss58_address}, "
            f"which does not match MINER_SS58 {ss58}"
        )
    return keypair
