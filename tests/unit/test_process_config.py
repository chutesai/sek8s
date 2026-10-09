"""process-config.py carries exactly one miner key from the config volume into the guest.

The config volume holds miner-ss58 plus either miner-private-key (128 hex, current hotkey files)
or miner-seed (64 hex). The key is copied to the k3s credentials dir and exported to
system-manager's miner.env; the other key never appears, even when an earlier boot left it on the
storage-backed credentials dir.
"""

import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "ansible/guest/roles/config/files/process-config.py"
)

# Throwaway Bittensor 11 hotkey (tests/rust/fixtures/hotkey-bittensor11-full.json).
SS58 = "5H1oTw5YnpYau6ViMURhEjS3Nar8AJc2wuzusNFbrWGpp4MP"
SEED = "30e940aa8b6ba8ff49951b7366326d60bfc64c9a7807787bca239b81009fb888"
PRIVATE_KEY = (
    "81433c30f96e2c3ca96165036d65d9deffa6fcc7eafdfa029162026abe91cc0c"
    "7acd542677534ebc6a8eddd23d6edbb8e6a4afadd5deccbf836ed5ba8229bf11"
)


@pytest.fixture
def pc(monkeypatch, tmp_path):
    """Load the script with every path it touches moved under tmp_path.

    Bytecode is off so the loader does not write a .pyc into the role's files/ directory.
    """
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    loader = importlib.machinery.SourceFileLoader("process_config", str(_SCRIPT))
    spec = importlib.util.spec_from_loader("process_config", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    volume = tmp_path / "config"
    creds = tmp_path / "credentials"
    volume.mkdir()
    monkeypatch.setattr(
        module,
        "EXPECTED_FILES",
        {name: str(volume / name) for name in module.EXPECTED_FILES},
    )
    monkeypatch.setattr(module, "MINER_CREDS_DIR", str(creds))
    monkeypatch.setattr(module, "MINER_SS58_TARGET", str(creds / "miner-ss58"))
    monkeypatch.setattr(module, "SYSTEM_MANAGER_MINER_ENV", str(tmp_path / "miner.env"))
    monkeypatch.setattr(module, "LOG_FILE", str(tmp_path / "log" / "validator.log"))
    monkeypatch.setattr(os, "chown", lambda *args, **kwargs: None)

    module.volume = volume
    module.creds = creds
    module.miner_env = tmp_path / "miner.env"
    return module


def _write_volume(pc, **files):
    for name, content in files.items():
        (pc.volume / name.replace("_", "-")).write_text(content + "\n")


def _apply(pc):
    creds = pc.read_miner_credentials()
    assert creds is not None
    assert pc.apply_miner_credentials(*creds)


def test_seed_only_volume_writes_exactly_what_it_did_before(pc):
    """Existing VMs: miner.env and the credentials dir are byte-identical to today's."""
    _write_volume(pc, miner_ss58=SS58, miner_seed=SEED)

    _apply(pc)

    assert pc.miner_env.read_text() == f"MINER_SS58={SS58}\nMINER_SEED={SEED}\n"
    assert sorted(p.name for p in pc.creds.iterdir()) == ["miner-seed", "miner-ss58"]
    assert (pc.creds / "miner-seed").read_text() == SEED + "\n"


def test_private_key_only_volume_exports_the_private_key(pc):
    _write_volume(pc, miner_ss58=SS58, miner_private_key=PRIVATE_KEY)

    _apply(pc)

    assert pc.miner_env.read_text() == (
        f"MINER_SS58={SS58}\nMINER_PRIVATE_KEY={PRIVATE_KEY}\n"
    )
    assert sorted(p.name for p in pc.creds.iterdir()) == [
        "miner-private-key",
        "miner-ss58",
    ]
    assert (pc.creds / "miner-private-key").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "previous, current, value",
    [
        ("miner-seed", "miner_private_key", PRIVATE_KEY),
        ("miner-private-key", "miner_seed", SEED),
    ],
)
def test_switching_keys_removes_the_previous_boots_copy(pc, previous, current, value):
    """The credentials dir lives on the storage volume and survives reboots."""
    pc.creds.mkdir()
    (pc.creds / previous).write_text("left over from an earlier boot\n")
    _write_volume(pc, miner_ss58=SS58, **{current: value})

    _apply(pc)

    assert not (pc.creds / previous).exists()
    assert (pc.creds / current.replace("_", "-")).read_text() == value + "\n"


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"miner_ss58": SS58, "miner_seed": SEED, "miner_private_key": PRIVATE_KEY},
        {"miner_ss58": SS58},
        {"miner_seed": SEED},
        {"miner_private_key": PRIVATE_KEY},
    ],
    ids=[
        "none",
        "both-keys",
        "ss58-only",
        "seed-without-ss58",
        "private-key-without-ss58",
    ],
)
def test_incomplete_or_ambiguous_credentials_are_rejected(pc, files):
    _write_volume(pc, **files)
    assert pc.read_miner_credentials() is None


@pytest.mark.parametrize(
    "key_file, content",
    [
        ("miner_private_key", PRIVATE_KEY[:127]),
        ("miner_private_key", PRIVATE_KEY + "0"),
        ("miner_private_key", f"0x{PRIVATE_KEY}"),
        ("miner_private_key", "g" + PRIVATE_KEY[1:]),
        ("miner_private_key", SEED),
        ("miner_seed", PRIVATE_KEY),
        ("miner_seed", f"0x{SEED}"),
    ],
    ids=[
        "private-key-127",
        "private-key-129",
        "private-key-0x",
        "private-key-non-hex",
        "seed-in-private-key-file",
        "private-key-in-seed-file",
        "seed-0x",
    ],
)
def test_malformed_keys_are_rejected(pc, key_file, content):
    _write_volume(pc, miner_ss58=SS58, **{key_file: content})
    assert pc.read_miner_credentials() is None


def test_invalid_ss58_is_rejected(pc):
    _write_volume(pc, miner_ss58="not-an-address", miner_private_key=PRIVATE_KEY)
    assert pc.read_miner_credentials() is None
