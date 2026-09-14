"""Static checks on the initramfs signing-key fetcher.

The script cannot be executed here — it needs an initramfs, `log_begin_msg`, a network
and a root key — so this asserts on its text.

The invariant below is a documentation-drift guard, and the drift it guards runs the
opposite way to the usual one. The published signing workflow describes `gpg
--detach-sign` and `gpgv`; the implementation is RSA PKCS#1 v1.5 over SHA-256 verified
with `openssl dgst`, because the root key lives in Cloud KMS and KMS cannot emit
OpenPGP packets. The code is correct and the document was stale. So the failure mode
worth pinning is someone reading that document and "fixing" the code to match it: a
bundle signed the documented way cannot be verified here, and an unverifiable bundle
powers the VM off (`fail()` runs `poweroff -f`).
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ansible/guest/roles/signing-keys/files/initramfs/fetch-signing-keys"


@pytest.fixture(scope="module")
def script() -> str:
    return SCRIPT.read_text()


def test_signature_verification_matches_the_root_key_format(script):
    """RSA via openssl against a PEM root key — not gpgv against a PGP signature."""
    assert 'ROOT_KEY="/etc/chutes/root-signing-key.pem"' in script
    assert "openssl dgst -sha256 -verify" in script
    assert "gpgv" not in script, (
        "verifier changed to gpgv; the signing workflow and the bundle it produces "
        "must change with it, or every VM powers off at the next key rotation"
    )


def test_a_failed_verification_is_terminal(script):
    """Verification failure must power the VM off, not continue with an unverified key."""
    assert "poweroff -f" in script
    fail_line = next(
        line
        for line in script.splitlines()
        if "RSA signature verification FAILED" in line
    )
    assert fail_line.strip().startswith(
        "fail "
    ), "signature failure no longer routes through fail(); an unverified key could be installed"
