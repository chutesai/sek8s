"""Run the launch gates without launching a VM — use before an upgrade to confirm
a node will relaunch and re-attest rather than going offline.

    python3 -m chutes.guest.verify              # relaunch as-is?
    python3 -m chutes.guest.verify --target-os 26.04   # ... after an OS upgrade?

Exit: 0 READY · 1 BLOCKED (won't relaunch: wrong QEMU or uncharacterized
topology) · 2 WARNING (gates pass but no measurement for this topology x QEMU).
"""

import argparse
import sys

from chutes.guest.detection import (
    SUPPORTED_QEMU_BY_OS,
    detect_profile,
    detect_qemu_version,
    verify_host_qemu_supported,
)

READY = 0
BLOCKED = 1
WARNING = 2


def verify_host(target_os: str | None = None) -> int:
    """Run the launch gates without launching; return one of READY/BLOCKED/WARNING."""
    # Gate A: which QEMU's measurement matters?
    if target_os is None:
        # As-is: host must be on the QEMU its current OS ships.
        try:
            verify_host_qemu_supported()
        except ValueError as exc:
            print(f"BLOCKED (QEMU): {exc}")
            return BLOCKED
        qemu_for_measurement = detect_qemu_version()
    else:
        # Pre-upgrade: check against the target OS's QEMU (the upgrade replaces it).
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

    # Gate B: topology hard-match (raises if uncharacterized).
    try:
        profile, fingerprint = detect_profile()
    except ValueError as exc:
        print(f"BLOCKED (topology): {exc}")
        return BLOCKED

    # Advisory: is there a registered MEASUREMENT for this exact fingerprint x QEMU?
    # (Gate B / detect_profile already BLOCKED a host whose fingerprint isn't baselined,
    # including a placeholder pending its cpu_processor_id capture — so we only reach
    # here for a baselined fingerprint, and just report which QEMU it's registered at.)
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
