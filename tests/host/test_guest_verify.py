"""Tests for the host-readiness verify entrypoint (chutes_cvm.guest.verify).

verify_host is now API-backed: Gate A is the local QEMU check, Gate B is a dry-run
preflight (run_preflight) whose status maps to READY/WARNING, and any preflight failure
fails closed to BLOCKED.
"""

from contextlib import ExitStack
from unittest.mock import patch

from chutes_cvm.guest import verify
from chutes_cvm.guest.preflight import PreflightError


def _patch(status="accepted", qemu_raises=False, preflight_raises=False):
    """Patch the QEMU gate and run_preflight. Returns (ExitStack, preflight_mock)."""
    stack = ExitStack()
    gate = stack.enter_context(
        patch("chutes_cvm.guest.verify.verify_host_qemu_supported")
    )
    if qemu_raises:
        gate.side_effect = ValueError("qemu 10.1.0 != expected 10.2.1")
    pf = stack.enter_context(patch("chutes_cvm.guest.verify.run_preflight"))
    if preflight_raises:
        pf.side_effect = PreflightError("API unreachable")
    else:
        pf.return_value = {"status": status, "detail": "d", "fingerprint": "fp"}
    return stack, gate, pf


def test_ready_when_accepted():
    stack, _, _ = _patch(status="accepted")
    with stack:
        assert verify.verify_host(scripts_dir="/x") == verify.READY


def test_warning_when_pending():
    stack, _, _ = _patch(status="pending")
    with stack:
        assert verify.verify_host(scripts_dir="/x") == verify.WARNING


def test_warning_when_unknown():
    stack, _, _ = _patch(status="unknown")
    with stack:
        assert verify.verify_host(scripts_dir="/x") == verify.WARNING


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


def test_blocked_when_target_os_unsupported():
    stack, _, pf = _patch()
    with stack:
        assert verify.verify_host(target_os="99.99", scripts_dir="/x") == verify.BLOCKED
        pf.assert_not_called()  # unsupported target fails before any preflight


def test_target_os_skips_live_qemu_gate_and_passes_target_qemu():
    # --target-os mode ignores the live QEMU (the upgrade replaces it), so even a raising
    # gate doesn't block; and the target's QEMU is what gets checked at the API.
    stack, gate, pf = _patch(status="accepted", qemu_raises=True)
    with stack:
        assert verify.verify_host(target_os="26.04", scripts_dir="/x") == verify.READY
        gate.assert_not_called()
        assert (
            pf.call_args.kwargs.get("target_qemu")
            == verify.SUPPORTED_QEMU_BY_OS["26.04"]
        )
        assert pf.call_args.kwargs.get("dry_run") is True
