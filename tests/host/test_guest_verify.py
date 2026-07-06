"""Tests for the standalone host-readiness verify entrypoint (chutes.guest.verify)."""

from unittest.mock import patch

from chutes.guest import verify
from chutes.guest.gpu.profiles import GPU_PROFILES

# ar6 topology: registered for H200 at QEMU 10.1.0 (see baselined_measurements).
_H200_AR6_FP = ("numa", (0, 0, 0, 0, 1, 1, 1, 1), (0, 0, 0, 0), ())


def _patch_verify(profile, fingerprint, qemu="10.1.0", qemu_raises=False):
    """Patch the verify module's collaborators. Returns an ExitStack."""
    from contextlib import ExitStack

    stack = ExitStack()
    qemu_gate = stack.enter_context(
        patch("chutes.guest.verify.verify_host_qemu_supported")
    )
    if qemu_raises:
        qemu_gate.side_effect = ValueError("qemu 10.2.1 != expected 10.1.0")
    stack.enter_context(patch("chutes.guest.verify.detect_profile", return_value=profile))
    stack.enter_context(patch("chutes.guest.verify.detect_qemu_version", return_value=qemu))
    stack.enter_context(patch("chutes.guest.verify._host_fingerprint", return_value=fingerprint))
    return stack


def test_verify_ready_when_measurement_registered():
    with _patch_verify(GPU_PROFILES["H200"], _H200_AR6_FP, qemu="10.1.0"):
        assert verify.verify_host() == verify.READY


def test_verify_blocked_when_qemu_gate_fails():
    with _patch_verify(GPU_PROFILES["H200"], _H200_AR6_FP, qemu_raises=True):
        assert verify.verify_host() == verify.BLOCKED


def test_verify_blocked_when_topology_uncharacterized():
    stack = _patch_verify(GPU_PROFILES["H200"], _H200_AR6_FP)
    with stack:
        with patch(
            "chutes.guest.verify.detect_profile",
            side_effect=ValueError("Host topology ... is not baselined"),
        ):
            assert verify.verify_host() == verify.BLOCKED


def test_verify_blocked_when_target_os_unsupported():
    # Unsupported target OS must fail before any topology work.
    with _patch_verify(GPU_PROFILES["H200"], _H200_AR6_FP):
        assert verify.verify_host(target_os="99.99") == verify.BLOCKED


def test_verify_warns_when_no_measurement_at_target_qemu():
    # H200 flat is registered at QEMU 10.1.0 (8xh200 [10.1.0-flat]) but there is
    # no 10.2.1 flat measurement -> upgrading a flat host to 26.04 (QEMU 10.2.1)
    # passes the launch gates but would 403 at attestation.
    h200_flat = ("flat", 8, 4, 0)
    with _patch_verify(GPU_PROFILES["H200"], h200_flat):
        assert verify.verify_host(target_os="26.04") == verify.WARNING


def test_verify_target_os_skips_live_qemu_gate():
    # In --target-os mode the live-QEMU hygiene gate must NOT run (the upgrade
    # replaces QEMU), so even a raising gate doesn't block a registered combo.
    xeon6 = GPU_PROFILES["B200_XEON6"]
    xeon6_fp = ("numa", (0, 0, 0, 0, 1, 1, 1, 1), (), ())  # registered at 10.2.1 (no IB)
    with _patch_verify(xeon6, xeon6_fp, qemu_raises=True):
        assert verify.verify_host(target_os="26.04") == verify.READY
