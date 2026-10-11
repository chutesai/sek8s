"""system-manager's MinerConfig signs with MINER_PRIVATE_KEY or MINER_SEED from miner.env.

Keys are the throwaway Bittensor 11 hotkey in tests/rust/fixtures (never a real miner key).
"""

import json
from pathlib import Path

import pytest
from sek8s_common.constants import HOTKEY_HEADER, NONCE_HEADER, SIGNATURE_HEADER
from substrateinterface import Keypair

from sek8s.config import MinerConfig
from sek8s.services.util import sign_request

_FULL = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "rust"
        / "fixtures"
        / "hotkey-bittensor11-full.json"
    ).read_text()
)
SS58 = _FULL["ss58Address"]
SEED = _FULL["secretSeed"].removeprefix("0x")
PRIVATE_KEY = _FULL["privateKey"].removeprefix("0x")


@pytest.fixture
def miner_env(monkeypatch):
    """miner.env as process-config writes it: MINER_SS58 plus exactly one key."""
    monkeypatch.delenv("MINER_SEED", raising=False)
    monkeypatch.delenv("MINER_PRIVATE_KEY", raising=False)

    def _set(**env):
        monkeypatch.setenv("MINER_SS58", SS58)
        for name, value in env.items():
            monkeypatch.setenv(name, value)

    return _set


@pytest.mark.parametrize(
    "env",
    [{"MINER_PRIVATE_KEY": PRIVATE_KEY}, {"MINER_SEED": SEED}],
    ids=["private-key", "seed"],
)
def test_sign_request_signs_as_the_hotkey(miner_env, env):
    miner_env(**env)

    headers, _ = sign_request(purpose="cache")

    assert headers[HOTKEY_HEADER] == SS58
    message = f"{SS58}:{headers[NONCE_HEADER]}:cache"
    assert Keypair(ss58_address=SS58).verify(
        message, bytes.fromhex(headers[SIGNATURE_HEADER])
    )


def test_a_key_for_another_hotkey_is_refused(miner_env, monkeypatch):
    miner_env(MINER_PRIVATE_KEY=PRIVATE_KEY)
    monkeypatch.setenv(
        "MINER_SS58", Keypair.create_from_seed("0x" + "cd" * 32).ss58_address
    )

    with pytest.raises(ValueError, match="does not match MINER_SS58"):
        MinerConfig().miner_keypair


def test_no_key_leaves_signing_unavailable(miner_env):
    miner_env()

    assert MinerConfig().miner_keypair is None
    with pytest.raises(ValueError, match="miner_keypair must be configured"):
        sign_request(purpose="cache")
