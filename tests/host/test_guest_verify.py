"""Tests for the host-readiness verify entrypoint (chutes_cvm.guest.verify).

verify_host is API-backed: Gate A is the local QEMU check; Gate B reads the image's
(version, rc) from its manifest and asks POST /servers/tdx/preflight (run_preflight) whether a
published measurement covers this host — ``launchable`` maps to READY/WARNING. Any preflight
failure, or an unreadable image manifest, fails closed to BLOCKED. ``--submit`` additionally
registers the class (submit_profile) when it is not yet launchable.
"""

from contextlib import ExitStack
from unittest.mock import patch

from chutes_cvm.guest import verify
from chutes_cvm.guest.preflight import PreflightError


def _patch(
    launchable=True,
    qemu_raises=False,
    preflight_raises=False,
    image_raises=False,
):
    """Patch the QEMU gate, image (version, rc) resolution, and run_preflight.
    Returns (ExitStack, gate_mock, preflight_mock)."""
    stack = ExitStack()
    gate = stack.enter_context(
        patch("chutes_cvm.guest.verify.verify_host_qemu_supported")
    )
    if qemu_raises:
        gate.side_effect = ValueError("qemu 10.1.0 != expected 10.2.1")
    img = stack.enter_context(patch("chutes_cvm.guest.verify._image_version_rc"))
    if image_raises:
        img.side_effect = FileNotFoundError("no manifest")
    else:
        img.return_value = ("1.4.0", False)
    pf = stack.enter_context(patch("chutes_cvm.guest.verify.run_preflight"))
    if preflight_raises:
        pf.side_effect = PreflightError("API unreachable")
    else:
        pf.return_value = {"launchable": launchable, "detail": "d", "fingerprint": "fp"}
    return stack, gate, pf


def test_ready_when_launchable():
    stack, _, _ = _patch(launchable=True)
    with stack:
        assert verify.verify_host(scripts_dir="/x") == verify.READY


def test_warning_when_not_launchable(capsys):
    # No measurement covers this image x host yet → WARNING, advise submit-profile.
    stack, _, _ = _patch(launchable=False)
    with stack:
        assert verify.verify_host(scripts_dir="/x") == verify.WARNING
    assert "submit-profile" in capsys.readouterr().out


def test_blocked_when_qemu_gate_fails():
    # The QEMU gate runs first (as-is mode); when it fails we never reach the API.
    stack, _, pf = _patch(qemu_raises=True)
    with stack:
        assert verify.verify_host(scripts_dir="/x") == verify.BLOCKED
        pf.assert_not_called()


def test_blocked_when_preflight_fails():
    # No verdict (transport/auth/API error) -> fail closed.
    stack, _, _ = _patch(preflight_raises=True)
    with stack:
        assert verify.verify_host(scripts_dir="/x") == verify.BLOCKED


def test_blocked_when_image_unreadable():
    # Can't determine what would boot -> can't check it -> fail closed, before any API call.
    stack, _, pf = _patch(image_raises=True)
    with stack:
        assert verify.verify_host(scripts_dir="/x") == verify.BLOCKED
        pf.assert_not_called()


def test_blocked_when_target_os_unsupported():
    stack, _, pf = _patch()
    with stack:
        assert verify.verify_host(target_os="99.99", scripts_dir="/x") == verify.BLOCKED
        pf.assert_not_called()  # unsupported target fails before any preflight


def test_target_os_skips_live_qemu_gate_and_passes_target_qemu():
    # --target-os mode ignores the live QEMU (the upgrade replaces it), so even a raising
    # gate doesn't block; and the target's QEMU is what gets checked at the API.
    stack, gate, pf = _patch(launchable=True, qemu_raises=True)
    with stack:
        assert verify.verify_host(target_os="26.04", scripts_dir="/x") == verify.READY
        gate.assert_not_called()
        assert (
            pf.call_args.kwargs.get("target_qemu")
            == verify.SUPPORTED_QEMU_BY_OS["26.04"]
        )
        assert pf.call_args.kwargs.get("version") == "1.4.0"
        assert pf.call_args.kwargs.get("rc") is False


def test_submit_registers_when_not_launchable():
    # `verify --submit` registers an unmeasured class via submit_profile; still WARNING.
    stack, _, _ = _patch(launchable=False)
    with stack, patch(
        "chutes_cvm.guest.verify.submit_profile",
        return_value={"status": "pending", "stored": True, "fingerprint": "fp"},
    ) as sub:
        assert verify.verify_host(scripts_dir="/x", submit=True) == verify.WARNING
        sub.assert_called_once()
