"""The initramfs attestation evidence: what boot attestation and /provision send the API.

The API takes the evidence from one `quote` field on every endpoint and tells a TDX quote from
an SEV-SNP report by its bytes, so both initramfs flows must send `quote` and must produce the
evidence for whichever TEE they boot on. The quote generators themselves need a TEE guest
device, so `tee-evidence` is sourced for its pure helpers and input checks, and the rest is
asserted on the scripts' text.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "ansible/guest/roles/prepare-boot-image"
INITRAMFS = ROLE / "files/initramfs"
TEE_EVIDENCE = INITRAMFS / "tee-evidence"

_LOG_STUBS = (
    'log_success_msg() { :; }; log_failure_msg() { echo "FAIL: $*" >&2; }; '
    "log_begin_msg() { :; }; log_end_msg() { :; }; "
)


def _run(snippet: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"{_LOG_STUBS} . {TEE_EVIDENCE}; {snippet}"],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "value, ok",
    [
        ("a" * 64, True),
        ("0123456789abcdefABCDEF" + "0" * 42, True),
        ("a" * 63, False),
        ("a" * 65, False),
        ("g" * 64, False),
        ("", False),
    ],
)
def test_is_hex64_accepts_only_one_32_byte_half(value, ok):
    assert (_run(f"is_hex64 '{value}'").returncode == 0) is ok


@pytest.mark.parametrize(
    "nonce, cert_hash, complaint",
    [
        ("a" * 63, "b" * 64, "Nonce is not 64 hex characters (got 63)"),
        ("a" * 128, "b" * 64, "Nonce is not 64 hex characters (got 128)"),
        ("a" * 64, "b" * 63, "Certificate hash is not 64 hex characters (got 63)"),
    ],
)
def test_generate_evidence_refuses_a_malformed_report_data_half(
    nonce, cert_hash, complaint
):
    """A wrong-width nonce used to be cut to 128 characters, shifting the cert hash out of the
    report data; the API then rejected the evidence with nothing pointing at the nonce.
    """
    result = _run(f"generate_evidence '{nonce}' '{cert_hash}'")
    assert result.returncode != 0
    assert complaint in result.stderr


def test_hex2bin_writes_raw_bytes():
    result = subprocess.run(
        ["bash", "-c", f"{_LOG_STUBS} . {TEE_EVIDENCE}; hex2bin 00ff41"],
        capture_output=True,
    )
    assert result.stdout == b"\x00\xffA"


def test_boot_attestation_sends_the_evidence_as_quote():
    """BootAttestationArgs requires `quote`; a platform-named field is a 422 at every boot."""
    script = (INITRAMFS / "attest-common").read_text()
    body = next(
        line for line in script.splitlines() if line.strip().startswith("body=")
    )
    assert body.strip().startswith('body="{\\"quote\\":\\"$QUOTE_B64\\"')
    assert '_quote\\"' not in body


@pytest.mark.parametrize("flow", ["attest-common", "provision-common"])
def test_both_flows_generate_evidence_through_tee_evidence(flow):
    """/provision used to call tdx-quote-generator directly, so an SEV-SNP guest failed it and
    powered off. Both flows now share the platform-aware generator."""
    script = (INITRAMFS / flow).read_text()
    assert ". /scripts/tee-evidence" in script
    assert "generate_evidence " in script
    assert "tdx-quote-generator" not in script


@pytest.mark.parametrize("tasks", ["luks_encrypt.yml", "debug_install.yml"])
def test_prod_and_debug_initramfs_install_tee_evidence(tasks):
    text = (ROLE / "tasks" / tasks).read_text()
    assert "src: files/initramfs/tee-evidence" in text
    assert (
        'dest: "{{ newroot_mount }}/etc/initramfs-tools/scripts/tee-evidence"' in text
    )
