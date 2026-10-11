"""sek8s_common.hotkey.load_miner_keypair: private key first, seed as fallback, and the keypair's
address must be MINER_SS58.

Keys are the throwaway Bittensor 11 hotkey in tests/rust/fixtures (never a real miner key).
"""

import json
from pathlib import Path

import pytest
from sek8s_common.hotkey import load_miner_keypair
from substrateinterface import Keypair

_FIXTURES = Path(__file__).resolve().parents[1] / "rust" / "fixtures"
_FULL = json.loads((_FIXTURES / "hotkey-bittensor11-full.json").read_text())
_SEEDLESS = json.loads((_FIXTURES / "hotkey-bittensor11-seedless.json").read_text())

SS58 = _FULL["ss58Address"]
SEED = _FULL["secretSeed"].removeprefix("0x")
PRIVATE_KEY = _FULL["privateKey"].removeprefix("0x")
OTHER = Keypair.create_from_seed("0x" + "cd" * 32)


def test_loads_from_the_seed():
    assert load_miner_keypair(SS58, None, SEED).ss58_address == SS58


def test_loads_from_the_private_key():
    keypair = load_miner_keypair(SS58, PRIVATE_KEY, None)
    assert keypair.ss58_address == SS58
    message = b"payload"
    assert Keypair(ss58_address=SS58).verify(message, keypair.sign(message))


def test_loads_a_seedless_hotkey_file():
    """The hotkey format the change is for: privateKey, no secretSeed."""
    keypair = load_miner_keypair(
        _SEEDLESS["ss58Address"], _SEEDLESS["privateKey"].removeprefix("0x"), None
    )
    assert keypair.ss58_address == _SEEDLESS["ss58Address"]


def test_prefers_the_private_key():
    keypair = load_miner_keypair(OTHER.ss58_address, OTHER.private_key.hex(), SEED)
    assert keypair.ss58_address == OTHER.ss58_address


def test_no_key_is_none():
    assert load_miner_keypair(SS58, None, None) is None
    assert load_miner_keypair(None, "", "") is None


@pytest.mark.parametrize(
    "private_key, seed",
    [(PRIVATE_KEY, None), (None, SEED)],
    ids=["private-key", "seed"],
)
def test_rejects_a_key_for_another_hotkey(private_key, seed):
    with pytest.raises(ValueError, match="does not match MINER_SS58"):
        load_miner_keypair(OTHER.ss58_address, private_key, seed)


def test_rejects_a_key_without_an_ss58():
    with pytest.raises(ValueError, match="MINER_SS58 is not"):
        load_miner_keypair(None, PRIVATE_KEY, None)


@pytest.mark.parametrize(
    "private_key, seed",
    [(PRIVATE_KEY[:126], None), ("zz" * 64, None), (None, "zz" * 32)],
    ids=["short-private-key", "non-hex-private-key", "non-hex-seed"],
)
def test_rejects_a_malformed_key_without_echoing_it(private_key, seed):
    with pytest.raises(ValueError, match="is not a valid sr25519 key") as exc:
        load_miner_keypair(SS58, private_key, seed)
    assert (private_key or seed) not in str(exc.value)
