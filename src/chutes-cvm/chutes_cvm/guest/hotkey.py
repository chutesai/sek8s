"""Miner hotkey credential: its format, and the keypair the host signs with.

The launch config carries exactly one credential for the miner hotkey: the 64-byte sr25519
private key (``miner.private_key``, a current Bittensor hotkey file's ``privateKey``) or the
32-byte seed (``miner.seed``, ``secretSeed``). Both are plain hex without ``0x``, the form the
guest's initramfs signer and process-config.py accept, so a config that passes here boots.
"""

from __future__ import annotations

import re

from substrateinterface import Keypair, KeypairType

SEED_HEX_LEN = 64
PRIVATE_KEY_HEX_LEN = 128
# Generic Substrate prefix, as Bittensor uses; miner ss58 addresses start with '5'.
SS58_FORMAT = 42

_HEX = re.compile(r"[0-9a-fA-F]+")


class HotkeyError(ValueError):
    """The miner credential is missing, malformed or not the configured hotkey's
    (message is user-facing and never contains key material)."""


def check_key_hex(value: str, hex_len: int, name: str) -> str:
    """Return ``value`` if it is exactly ``hex_len`` hex characters with no 0x prefix."""
    if value[:2] in ("0x", "0X"):
        raise HotkeyError(f"{name} must not have a 0x prefix")
    if len(value) != hex_len or not _HEX.fullmatch(value):
        raise HotkeyError(
            f"{name} must be {hex_len} hex characters (got {len(value)} characters)"
        )
    return value


def miner_keypair(ss58: str, private_key: str, seed: str) -> Keypair:
    """The miner keypair from exactly one of ``private_key`` or ``seed``, checked against
    ``ss58`` so the host never signs as a hotkey other than the configured one."""
    if private_key and seed:
        raise HotkeyError("set miner.private_key or miner.seed, not both")
    if not private_key and not seed:
        raise HotkeyError("config has no miner.private_key or miner.seed")
    name = "miner.private_key" if private_key else "miner.seed"
    try:
        if private_key:
            keypair = Keypair.create_from_private_key(
                private_key, ss58_format=SS58_FORMAT, crypto_type=KeypairType.SR25519
            )
        else:
            keypair = Keypair.create_from_seed(
                seed, ss58_format=SS58_FORMAT, crypto_type=KeypairType.SR25519
            )
    # substrate-interface raises assorted exception types on malformed input.
    except Exception as exc:
        raise HotkeyError(
            f"{name} is not a valid sr25519 key ({type(exc).__name__})"
        ) from None
    if keypair.ss58_address != ss58:
        raise HotkeyError(
            f"{name} belongs to hotkey {keypair.ss58_address}, not miner.ss58 {ss58 or '(unset)'}"
        )
    return keypair
