"""Tests for the standalone host-readiness verify entrypoint (chutes_cvm.guest.verify)."""

from unittest.mock import patch

from chutes_cvm.guest import verify
from chutes_cvm.guest.gpu import known_topologies as known
from chutes_cvm.guest.gpu.profiles import GPU_PROFILES
from chutes_cvm.guest.gpu.topology import (
    CpuTopology,
    FlatTopology,
    NumaTopology,
    TopologyFingerprint,
)

# H200 host shape (128-CPU dev-h200-tee): 124 vcpus, 1128 GB, Emerald Rapids. Matches
# H200Profile.baselined_measurements exactly, so it reads as a registered measurement.
_H200_SHAPE = dict(
    vcpus=124,
    sockets=2,
    cpu_vendor="GenuineIntel",
    cpu_processor_id="f2060c00fffba91f",
)
# ar6/KR6288 topology: registered for H200 at QEMU 10.2.1 (nvswitches on node 0).
_H200_AR6_FP = known.H200_KR6288


def _patch_verify(profile, fingerprint, qemu="10.2.1", qemu_raises=False):
    """Patch the verify module's collaborators. Returns an ExitStack.

    detect_profile now returns the (profile, fingerprint) pair, so the mock returns
    both — there is no separate fingerprint recompute to patch.
    """
    from contextlib import ExitStack

    stack = ExitStack()
    qemu_gate = stack.enter_context(
        patch("chutes_cvm.guest.verify.verify_host_qemu_supported")
    )
    if qemu_raises:
        qemu_gate.side_effect = ValueError("qemu 10.1.0 != expected 10.2.1")
    stack.enter_context(
        patch(
            "chutes_cvm.guest.verify.detect_profile",
            return_value=(profile, fingerprint),
        )
    )
    stack.enter_context(
        patch("chutes_cvm.guest.verify.detect_qemu_version", return_value=qemu)
    )
    return stack


def test_verify_ready_when_measurement_registered():
    with _patch_verify(GPU_PROFILES["H200"], _H200_AR6_FP, qemu="10.2.1"):
        assert verify.verify_host() == verify.READY


def test_verify_blocked_when_qemu_gate_fails():
    with _patch_verify(GPU_PROFILES["H200"], _H200_AR6_FP, qemu_raises=True):
        assert verify.verify_host() == verify.BLOCKED


def test_verify_blocked_when_topology_uncharacterized():
    stack = _patch_verify(GPU_PROFILES["H200"], _H200_AR6_FP)
    with stack:
        with patch(
            "chutes_cvm.guest.verify.detect_profile",
            side_effect=ValueError("Host fingerprint ... is not baselined"),
        ):
            assert verify.verify_host() == verify.BLOCKED


def test_verify_blocked_when_target_os_unsupported():
    # Unsupported target OS must fail before any topology work.
    with _patch_verify(GPU_PROFILES["H200"], _H200_AR6_FP):
        assert verify.verify_host(target_os="99.99") == verify.BLOCKED


def test_verify_warns_when_no_measurement_for_topology():
    # A flat-path H200 (>2 NUMA nodes) has no registered measurement at 10.2.1,
    # the only supported QEMU -> the gates pass but it would 403 at attestation.
    h200_flat = TopologyFingerprint(
        CpuTopology(**_H200_SHAPE), 1128, FlatTopology(gpu_count=8, nvswitch_count=4)
    )
    with _patch_verify(GPU_PROFILES["H200"], h200_flat):
        assert verify.verify_host(target_os="26.04") == verify.WARNING


def test_verify_warns_when_measurement_only_at_another_qemu():
    # Registered at some other QEMU but not the target's: still a WARNING, and
    # the operator is told where it *is* registered. Uses a stub profile because
    # 10.2.1 is currently the only QEMU any shipped profile is baselined at.
    fp = TopologyFingerprint(
        CpuTopology(**_H200_SHAPE),
        1128,
        NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1)),
    )

    class _StubProfile:
        name = "STUB"
        baselined_measurements = {"10.1.0": {fp}}

    with _patch_verify(_StubProfile(), fp):
        assert verify.verify_host(target_os="26.04") == verify.WARNING


def test_verify_target_os_skips_live_qemu_gate():
    # In --target-os mode the live-QEMU hygiene gate must NOT run (the upgrade
    # replaces QEMU), so even a raising gate doesn't block a registered combo.
    h200 = GPU_PROFILES["H200"]
    with _patch_verify(h200, _H200_AR6_FP, qemu_raises=True):
        assert verify.verify_host(target_os="26.04") == verify.READY
