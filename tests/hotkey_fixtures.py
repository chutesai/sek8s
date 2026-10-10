"""The throwaway Bittensor 11 hotkey from tests/rust/fixtures (never a real miner key), as the
host tooling carries it: ss58 plus seed or private key, hex without 0x."""

import json
from pathlib import Path

_FULL = json.loads(
    (
        Path(__file__).resolve().parent / "rust/fixtures/hotkey-bittensor11-full.json"
    ).read_text()
)

SS58 = _FULL["ss58Address"]
SEED = _FULL["secretSeed"].removeprefix("0x")
PRIVATE_KEY = _FULL["privateKey"].removeprefix("0x")
