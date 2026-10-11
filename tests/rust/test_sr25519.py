"""Cross-check the sr25519 binary against substrate-interface.

The Rust binary exists so the guest initramfs can prove possession of the miner
hotkey without the Python substrate stack. That is only useful if it produces
*exactly* what `substrateinterface.Keypair` produces, so these tests drive the
real library as the oracle rather than asserting against hardcoded vectors.

The fixtures are one throwaway hotkey (never a real miner key) written by Bittensor 11.3.0:
`hotkey-bittensor11-full.json` from `Wallet.create_new_hotkey()` with `secretPhrase`
removed, and `hotkey-bittensor11-seedless.json` from
`Wallet.regenerate_hotkey(private_key=...)`, the hotkey file format that has no
`secretSeed`.

Skipped when the binary has not been built; build it with:

    cd src/sr25519 && cargo build --release
"""

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from substrateinterface import Keypair, KeypairType

REPO_ROOT = Path(__file__).resolve().parents[2]
CRATE_DIR = REPO_ROOT / "src" / "sr25519"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
HOTKEY_SIGN = (
    REPO_ROOT / "ansible/guest/roles/prepare-boot-image/files/initramfs/hotkey-sign"
)

# Well-known Substrate development seeds, plus one arbitrary seed, so a bug that
# only shows up for particular scalar values has a chance to surface.
SEEDS = [
    "e5be9a5092b81bca64be81d212e7f2f9eba183bb7a90954f7b76361f6edb5c0a",  # //Alice
    "398f0c28f98885e046333d4a41c19cee4c37368a9832c6502f6cfd182e2aef89",  # //Bob
    "0000000000000000000000000000000000000000000000000000000000000001",
    "7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f",
]


def _load_hotkey(name: str) -> dict:
    return json.loads((FIXTURES / f"hotkey-bittensor11-{name}.json").read_text())


BT11_FULL = _load_hotkey("full")
BT11_SEEDLESS = _load_hotkey("seedless")

# Private keys as substrate-interface derives them from SEEDS, plus the seedless Bittensor 11
# hotkey's privateKey as written to disk (0x stripped, as the host tooling does).
PRIVATE_KEYS = [
    Keypair.create_from_seed(
        f"0x{seed}", crypto_type=KeypairType.SR25519
    ).private_key.hex()
    for seed in SEEDS
] + [BT11_SEEDLESS["privateKey"].removeprefix("0x")]


def _find_binary() -> str | None:
    """Locate sr25519: explicit override, cargo output dir, then PATH."""
    override = os.environ.get("SR25519_BIN")
    if override:
        return override if Path(override).is_file() else None
    # The static musl build is the artifact that actually ships into the initramfs,
    # so prefer it: a passing run against target/release proves less than one against
    # the binary that gets measured.
    candidates = [
        CRATE_DIR / "target" / "x86_64-unknown-linux-musl" / "release" / "sr25519",
        CRATE_DIR / "target" / "release" / "sr25519",
        CRATE_DIR / "target" / "debug" / "sr25519",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("sr25519")


BINARY = _find_binary()

pytestmark = pytest.mark.skipif(
    BINARY is None,
    reason="sr25519 not built (cd src/sr25519 && cargo build --release)",
)


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run([BINARY, *args], capture_output=True, text=True, timeout=30)
    if check and result.returncode != 0:
        raise AssertionError(f"sr25519 {args} failed: {result.stderr.strip()}")
    return result


def _key_file_writer(tmp_path, name):
    def _write(key_hex: str) -> str:
        path = tmp_path / name
        path.write_text(key_hex)
        path.chmod(0o600)
        return str(path)

    return _write


@pytest.fixture
def seed_file(tmp_path):
    """Write a seed to a 0600 file and return its path, as the initramfs will."""
    return _key_file_writer(tmp_path, "miner-seed")


@pytest.fixture
def private_key_file(tmp_path):
    """Write a private key to a 0600 file and return its path."""
    return _key_file_writer(tmp_path, "miner-private-key")


def _keypair_from_private_key(private_key: str) -> Keypair:
    return Keypair.create_from_private_key(
        f"0x{private_key}", ss58_format=42, crypto_type=KeypairType.SR25519
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_address_matches_substrate_interface(seed, seed_file):
    """Seed expansion + SS58 encoding agree with the Python library.

    This is the check that catches ExpansionMode::Uniform being used by mistake:
    it produces a valid address that is simply not the miner's hotkey.
    """
    expected = Keypair.create_from_seed(
        f"0x{seed}", crypto_type=KeypairType.SR25519
    ).ss58_address
    assert run("address", "--seed-file", seed_file(seed)).stdout.strip() == expected


@pytest.mark.parametrize("seed", SEEDS)
def test_rust_signature_verifies_in_python(seed, seed_file):
    """A signature the initramfs makes must verify server-side (Python)."""
    keypair = Keypair.create_from_seed(f"0x{seed}", crypto_type=KeypairType.SR25519)
    message = f"{keypair.ss58_address}:{int(time.time())}:attest"

    signature = run(
        "sign", "--seed-file", seed_file(seed), "--message", message
    ).stdout.strip()

    assert len(signature) == 128
    assert keypair.verify(message, bytes.fromhex(signature))


@pytest.mark.parametrize("seed", SEEDS)
def test_python_signature_verifies_in_rust(seed, seed_file):
    """The inverse direction, so `verify` is usable as a self-test on the host."""
    keypair = Keypair.create_from_seed(f"0x{seed}", crypto_type=KeypairType.SR25519)
    message = "provision:vm-01"
    signature = keypair.sign(message).hex()

    result = run(
        "verify",
        "--address",
        keypair.ss58_address,
        "--signature",
        signature,
        "--message",
        message,
    )
    assert result.stdout.strip() == "OK"


def test_signs_the_sek8s_auth_message_shape(seed_file):
    """Exercise the real `{ss58}:{nonce}:{sha256(body)}` payload from services/util.py."""
    seed = SEEDS[0]
    keypair = Keypair.create_from_seed(f"0x{seed}", crypto_type=KeypairType.SR25519)
    body = b'{"quote":"...","vm_name":"chutes-01","first_boot":true}'
    message = f"{keypair.ss58_address}:1756600000:{hashlib.sha256(body).hexdigest()}"

    signature = run(
        "sign", "--seed-file", seed_file(seed), "--message", message
    ).stdout.strip()
    assert keypair.verify(message, bytes.fromhex(signature))


def test_tampered_message_fails_verification(seed_file):
    seed = SEEDS[0]
    keypair = Keypair.create_from_seed(f"0x{seed}", crypto_type=KeypairType.SR25519)
    signature = run(
        "sign", "--seed-file", seed_file(seed), "--message", "original"
    ).stdout.strip()

    result = run(
        "verify",
        "--address",
        keypair.ss58_address,
        "--signature",
        signature,
        "--message",
        "tampered",
        check=False,
    )
    assert result.returncode != 0
    assert "verification failed" in result.stderr


def test_signature_from_a_different_seed_is_rejected(seed_file):
    """The whole point: a VM without the seed cannot sign for that hotkey."""
    victim = Keypair.create_from_seed(f"0x{SEEDS[0]}", crypto_type=KeypairType.SR25519)
    message = f"{victim.ss58_address}:1756600000:attest"

    # Attacker claims the victim's address but signs with their own seed.
    attacker_signature = run(
        "sign", "--seed-file", seed_file(SEEDS[1]), "--message", message
    ).stdout.strip()

    result = run(
        "verify",
        "--address",
        victim.ss58_address,
        "--signature",
        attacker_signature,
        "--message",
        message,
        check=False,
    )
    assert result.returncode != 0


def test_message_can_be_read_from_stdin(seed_file):
    """--message-file - keeps large or binary payloads off argv."""
    seed = SEEDS[0]
    keypair = Keypair.create_from_seed(f"0x{seed}", crypto_type=KeypairType.SR25519)
    message = b"bytes-from-stdin"

    result = subprocess.run(
        [BINARY, "sign", "--seed-file", seed_file(seed), "--message-file", "-"],
        input=message,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert keypair.verify(message, bytes.fromhex(result.stdout.decode().strip()))


def test_signatures_are_randomized_and_all_verify(seed_file):
    """sr25519 witnesses are random; never assert on signature-byte equality."""
    seed = SEEDS[0]
    keypair = Keypair.create_from_seed(f"0x{seed}", crypto_type=KeypairType.SR25519)
    path = seed_file(seed)

    signatures = {
        run("sign", "--seed-file", path, "--message", "repeat").stdout.strip()
        for _ in range(3)
    }
    assert len(signatures) == 3
    for signature in signatures:
        assert keypair.verify("repeat", bytes.fromhex(signature))


@pytest.mark.parametrize(
    "seed_content,expected_error",
    [
        ("deadbeef", "seed must be 32 bytes"),
        ("zz" * 32, "invalid hex character"),
        ("ab" * 33, "seed must be 32 bytes"),
        (f"0x{SEEDS[0]}", "seed must not have a 0x prefix"),
    ],
)
def test_malformed_seed_is_rejected(seed_content, expected_error, seed_file):
    """0x is rejected, as process-config.py rejects it, so the initramfs never accepts a key
    that userspace refuses after the LUKS rotation."""
    result = run("address", "--seed-file", seed_file(seed_content), check=False)
    assert result.returncode != 0
    assert expected_error in result.stderr


def test_missing_key_flag_errors_clearly():
    result = run("sign", "--message", "x", check=False)
    assert result.returncode != 0
    assert "one of --seed-file or --private-key-file is required" in result.stderr


def test_seed_and_private_key_flags_are_mutually_exclusive(seed_file, private_key_file):
    result = run(
        "address",
        "--seed-file",
        seed_file(SEEDS[0]),
        "--private-key-file",
        private_key_file(PRIVATE_KEYS[0]),
        check=False,
    )
    assert result.returncode != 0
    assert "mutually exclusive" in result.stderr


def test_help_lists_the_verbs_and_key_flags():
    output = run("--help").stdout
    for word in ("address", "sign", "verify", "--seed-file", "--private-key-file"):
        assert word in output


# ── Private keys (hotkey files without secretSeed) ──────────────────────────────


@pytest.mark.parametrize("private_key", PRIVATE_KEYS)
def test_private_key_address_matches_substrate_interface(private_key, private_key_file):
    """The silent-failure check for the key layout: from_ed25519_bytes would load the same
    bytes without error and print another hotkey's address."""
    expected = _keypair_from_private_key(private_key).ss58_address
    result = run("address", "--private-key-file", private_key_file(private_key))
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("private_key", PRIVATE_KEYS)
def test_rust_private_key_signature_verifies_in_python(private_key, private_key_file):
    keypair = _keypair_from_private_key(private_key)
    message = f"{keypair.ss58_address}:{int(time.time())}:attest"

    signature = run(
        "sign",
        "--private-key-file",
        private_key_file(private_key),
        "--message",
        message,
    ).stdout.strip()

    assert keypair.verify(message, bytes.fromhex(signature))


@pytest.mark.parametrize("private_key", PRIVATE_KEYS)
def test_python_private_key_signature_verifies_in_rust(private_key):
    keypair = _keypair_from_private_key(private_key)
    message = "provision:vm-01"

    result = run(
        "verify",
        "--address",
        keypair.ss58_address,
        "--signature",
        keypair.sign(message).hex(),
        "--message",
        message,
    )
    assert result.stdout.strip() == "OK"


def test_seedless_hotkey_file_address_is_reproduced(private_key_file):
    """The new hotkey file format end to end: its privateKey gives its own ss58Address."""
    private_key = BT11_SEEDLESS["privateKey"].removeprefix("0x")
    result = run("address", "--private-key-file", private_key_file(private_key))
    assert result.stdout.strip() == BT11_SEEDLESS["ss58Address"]


def test_seed_and_private_key_of_one_hotkey_agree(seed_file, private_key_file):
    """Both credentials of one Bittensor 11 hotkey give its address, in Rust and in Python."""
    seed = BT11_FULL["secretSeed"].removeprefix("0x")
    private_key = BT11_FULL["privateKey"].removeprefix("0x")
    expected = BT11_FULL["ss58Address"]

    assert run("address", "--seed-file", seed_file(seed)).stdout.strip() == expected
    assert (
        run(
            "address", "--private-key-file", private_key_file(private_key)
        ).stdout.strip()
        == expected
    )
    seed_keypair = Keypair.create_from_seed(
        f"0x{seed}", crypto_type=KeypairType.SR25519
    )
    assert seed_keypair.ss58_address == expected
    assert _keypair_from_private_key(private_key).ss58_address == expected


@pytest.mark.parametrize(
    "key_content,expected_error",
    [
        (PRIVATE_KEYS[0][:127], "odd length"),
        (PRIVATE_KEYS[0][:126], "private key must be 64 bytes"),
        (SEEDS[0], "private key must be 64 bytes"),
        (f"0x{PRIVATE_KEYS[0]}", "private key must not have a 0x prefix"),
        ("zz" * 64, "invalid hex character"),
    ],
)
def test_malformed_private_key_is_rejected(
    key_content, expected_error, private_key_file
):
    result = run(
        "address", "--private-key-file", private_key_file(key_content), check=False
    )
    assert result.returncode != 0
    assert expected_error in result.stderr


# ── hotkey-sign (the initramfs wrapper) ──────────────────────────────────────────


def _hotkey_sign(snippet: str, run_dir: Path) -> subprocess.CompletedProcess:
    """Source hotkey-sign with the stash paths moved off /run and sr25519 on PATH."""
    script = (
        f". {HOTKEY_SIGN}; "
        f'HOTKEY_SEED="{run_dir}/miner-seed"; '
        f'HOTKEY_PRIVATE_KEY="{run_dir}/miner-private-key"; '
        f"{snippet}"
    )
    env = {**os.environ, "PATH": f"{Path(BINARY).parent}:{os.environ['PATH']}"}
    return subprocess.run(
        ["sh", "-c", script], capture_output=True, text=True, env=env, timeout=30
    )


@pytest.fixture
def config_mount(tmp_path):
    mount = tmp_path / "tdx-config"
    mount.mkdir()
    return mount


def test_hotkey_sign_stashes_and_signs_with_a_private_key(config_mount, tmp_path):
    """A private-key-only config volume proves possession of the hotkey in the initramfs."""
    private_key = BT11_SEEDLESS["privateKey"].removeprefix("0x")
    (config_mount / "miner-private-key").write_text(private_key + "\n")
    run_dir = tmp_path / "run"
    keypair = _keypair_from_private_key(private_key)
    body = '{"vm_name":"chutes-01"}'
    message = f"{keypair.ss58_address}:n1:{hashlib.sha256(body.encode()).hexdigest()}"

    result = _hotkey_sign(
        f"hotkey_stash_key {config_mount} && hotkey_ss58 && hotkey_sign n1 '{body}'",
        run_dir,
    )

    assert result.returncode == 0, result.stderr
    ss58, signature = result.stdout.split()
    assert ss58 == BT11_SEEDLESS["ss58Address"]
    assert keypair.verify(message, bytes.fromhex(signature))
    assert sorted(p.name for p in run_dir.iterdir()) == ["miner-private-key"]


def test_hotkey_sign_rejects_a_volume_with_both_keys(config_mount, tmp_path):
    """Two credentials is a misconfiguration; fail before attestation, stashing nothing."""
    (config_mount / "miner-seed").write_text(SEEDS[0])
    (config_mount / "miner-private-key").write_text(PRIVATE_KEYS[0])
    run_dir = tmp_path / "run"

    result = _hotkey_sign(f"hotkey_stash_key {config_mount}", run_dir)

    assert result.returncode == 1
    assert not run_dir.exists()


def test_hotkey_sign_stashes_and_signs_with_a_seed(config_mount, tmp_path):
    (config_mount / "miner-seed").write_text(SEEDS[0])
    run_dir = tmp_path / "run"

    result = _hotkey_sign(f"hotkey_stash_key {config_mount} && hotkey_ss58", run_dir)

    assert result.returncode == 0, result.stderr
    expected = Keypair.create_from_seed(
        f"0x{SEEDS[0]}", crypto_type=KeypairType.SR25519
    )
    assert result.stdout.strip() == expected.ss58_address
    assert sorted(p.name for p in run_dir.iterdir()) == ["miner-seed"]


def test_hotkey_sign_fails_without_a_key(config_mount, tmp_path):
    result = _hotkey_sign(f"hotkey_stash_key {config_mount}", tmp_path / "run")
    assert result.returncode == 1


def test_hotkey_sign_fails_on_an_invalid_key(config_mount, tmp_path):
    (config_mount / "miner-private-key").write_text(f"0x{PRIVATE_KEYS[0]}")
    result = _hotkey_sign(f"hotkey_stash_key {config_mount}", tmp_path / "run")
    assert result.returncode == 1


def test_hotkey_sign_shreds_both_stashed_keys(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "miner-seed").write_text(SEEDS[0])
    (run_dir / "miner-private-key").write_text(PRIVATE_KEYS[0])

    result = _hotkey_sign("hotkey_shred_key && hotkey_sign n1 body", run_dir)

    assert result.returncode == 0
    assert result.stdout == ""
    assert list(run_dir.iterdir()) == []
