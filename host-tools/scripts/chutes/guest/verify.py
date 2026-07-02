"""Standalone host-readiness verification — the launch gates, without launching.

Run this BEFORE upgrading a host so a drain + upgrade can't take a node offline
that couldn't relaunch and re-attest afterwards. It runs the exact checks the
launch path runs (``verify_host_qemu_supported`` + ``detect_profile``) plus a
per-QEMU measurement advisory, but never starts a VM.

Usage:
    python3 -m chutes.guest.verify              # will this host relaunch as-is?
    python3 -m chutes.guest.verify --target-os 26.04   # ... after an OS upgrade?

Exit codes:
    0  READY    — topology characterized AND a registered measurement exists for
                  (topology x QEMU); the host should relaunch and attest.
    1  BLOCKED  — a launch gate fails (QEMU wrong for OS, or topology not
                  characterized). Do NOT upgrade; the VM would not relaunch.
    2  WARNING  — launch gates pass, but no REGISTERED measurement exists for this
                  (topology x QEMU). The VM would launch but 403 at attestation
                  until Chutes registers the measurement. Do NOT upgrade yet.
"""

import argparse
import sys

from chutes.guest.detection import (
    SUPPORTED_QEMU_BY_OS,
    detect_cx7_bridge_pfs,
    detect_infiniband_pfs,
    detect_nvidia_gpus,
    detect_nvswitches,
    detect_profile,
    detect_qemu_version,
    get_gpu_bdfs,
    host_topology_fingerprint,
    verify_host_qemu_supported,
)

READY = 0
BLOCKED = 1
WARNING = 2


def _host_fingerprint(profile) -> tuple:
    """Recompute the host's topology fingerprint for the resolved profile.

    Mirrors the device gathering in ``detect_profile`` (which validated the
    topology but does not return the fingerprint), using only the passthrough
    sets the profile actually launches with.
    """
    gpu_bdfs = get_gpu_bdfs() or detect_nvidia_gpus()
    total_gpus = len(gpu_bdfs)
    nvswitch_bdfs = (
        detect_nvswitches() if profile.should_passthrough_nvswitches(total_gpus) else []
    )
    ib_bdfs = (
        detect_infiniband_pfs(exclude_bdfs=detect_cx7_bridge_pfs())
        if profile.should_passthrough_infiniband
        else []
    )
    return host_topology_fingerprint(profile, gpu_bdfs, nvswitch_bdfs, ib_bdfs)


def verify_host(target_os: str | None = None) -> int:
    """Run the launch gates without launching; return one of READY/BLOCKED/WARNING."""
    # ── Gate A: QEMU. Which QEMU's measurement matters?
    if target_os is None:
        # As-is: the host must be on the QEMU its current OS ships.
        try:
            verify_host_qemu_supported()
        except ValueError as exc:
            print(f"BLOCKED (QEMU): {exc}")
            return BLOCKED
        qemu_for_measurement = detect_qemu_version()
    else:
        # Pre-upgrade: the upgrade WILL change QEMU, so we check the topology
        # against the QEMU the target OS ships rather than the live one.
        expected = SUPPORTED_QEMU_BY_OS.get(target_os)
        if expected is None:
            print(
                f"BLOCKED: target OS {target_os!r} is not supported "
                f"{sorted(SUPPORTED_QEMU_BY_OS)}. Upgrading to it would leave the "
                f"host on an unbaselined QEMU."
            )
            return BLOCKED
        qemu_for_measurement = expected
        print(
            f"Checking against target OS {target_os} (ships QEMU {expected}); "
            f"the live QEMU is ignored because the upgrade replaces it."
        )

    # ── Gate B: topology hard-match (the launch check; raises if uncharacterized).
    try:
        profile = detect_profile()
    except ValueError as exc:
        print(f"BLOCKED (topology): {exc}")
        return BLOCKED

    # ── Advisory: is a measurement REGISTERED for (this topology x that QEMU)?
    fingerprint = _host_fingerprint(profile)
    measured = profile.baselined_measurements
    if fingerprint in measured.get(qemu_for_measurement, set()):
        print(
            f"READY: {profile.name} topology {fingerprint} has a registered "
            f"measurement at QEMU {qemu_for_measurement}."
        )
        return READY

    other = sorted(q for q, topos in measured.items() if fingerprint in topos)
    print(
        f"WARNING: {profile.name} topology is characterized, but NO registered "
        f"measurement exists at QEMU {qemu_for_measurement}."
    )
    if other:
        print(
            f"  It IS registered at QEMU {other}. Relaunching under "
            f"{qemu_for_measurement} would attest with an unregistered RTMR0 and "
            f"be rejected (403) until the measurement is added."
        )
    print(
        "  Run discover-profile.sh and submit the output so Chutes can register "
        "this (topology x QEMU) before you upgrade."
    )
    return WARNING


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m chutes.guest.verify",
        description="Verify this host will relaunch and re-attest — without launching a VM.",
    )
    parser.add_argument(
        "--target-os",
        type=str,
        default=None,
        metavar="VERSION_ID",
        help="Check against the QEMU an OS upgrade would bring (e.g. 26.04) "
        "instead of the live QEMU. Use before an OS upgrade.",
    )
    args = parser.parse_args()
    return verify_host(target_os=args.target_os)


if __name__ == "__main__":
    sys.exit(main())
