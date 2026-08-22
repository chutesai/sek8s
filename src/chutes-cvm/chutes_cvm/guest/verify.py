"""Run the launch gates without launching a VM — use before an upgrade to confirm
a node will relaunch and re-attest rather than going offline.

    python3 -m chutes_cvm.guest.verify              # relaunch as-is?
    python3 -m chutes_cvm.guest.verify --target-os 26.04   # ... after an OS upgrade?

Two gates: (A) the host runs the QEMU its OS release baselines (local), and (B) the
control plane has a published measurement for this host class (the API preflight — the
same submit endpoint the miner uses, run as a non-storing dry-run check).

Exit: 0 READY · 1 BLOCKED (won't relaunch: wrong QEMU, or preflight couldn't run) ·
2 WARNING (gates run, but no published measurement for this topology x QEMU yet).
"""

import argparse
import os
import sys
from pathlib import Path

from chutes_cvm.guest.detection import SUPPORTED_QEMU_BY_OS, verify_host_qemu_supported
from chutes_cvm.guest.preflight import (
    DEFAULT_API_BASE,
    PreflightError,
    default_config_path,
    run_preflight,
)

READY = 0
BLOCKED = 1
WARNING = 2


def _resolve_scripts_dir() -> str:
    """host-tools/scripts (for discover-profile.sh + config.yaml). Mirrors cli._SCRIPTS_DIR."""
    return os.environ.get("CHUTES_CVM_SCRIPTS_DIR") or str(
        Path(__file__).resolve().parents[4] / "host-tools" / "scripts"
    )


def verify_host(
    target_os: "str | None" = None,
    scripts_dir: "str | None" = None,
    config_path: "str | None" = None,
    api_base: "str | None" = None,
) -> int:
    """Run the launch gates without launching; return one of READY/BLOCKED/WARNING."""
    scripts_dir = scripts_dir or _resolve_scripts_dir()

    # Gate A: which QEMU's measurement matters?
    if target_os is None:
        # As-is: the host must be on the QEMU its current OS ships.
        try:
            verify_host_qemu_supported()
        except ValueError as exc:
            print(f"BLOCKED (QEMU): {exc}")
            return BLOCKED
        target_qemu = None  # the live QEMU is already in the discovered profile
    else:
        # Pre-upgrade: check against the target OS's QEMU (the upgrade replaces the live one).
        expected = SUPPORTED_QEMU_BY_OS.get(target_os)
        if expected is None:
            print(
                f"BLOCKED: target OS {target_os!r} is not supported "
                f"{sorted(SUPPORTED_QEMU_BY_OS)}. Upgrading to it would leave the "
                f"host on an unbaselined QEMU."
            )
            return BLOCKED
        target_qemu = expected
        print(
            f"Checking against target OS {target_os} (ships QEMU {expected}); "
            f"the live QEMU is ignored because the upgrade replaces it."
        )

    # Gate B: does the control plane have a published measurement for this host class?
    # A dry-run preflight — capture metadata, sign, ask — without submitting (this is a
    # check, not a request to baseline). The API owns the fingerprint and the verdict.
    config = config_path or default_config_path(scripts_dir)
    api = api_base or os.environ.get("CHUTES_API_BASE") or DEFAULT_API_BASE
    try:
        resp = run_preflight(
            config_path=config,
            scripts_dir=scripts_dir,
            api_base=api,
            dry_run=True,
            target_qemu=target_qemu,
        )
    except PreflightError as exc:
        # Fail closed: if we cannot get a verdict, the host would attest into the unknown.
        print(f"BLOCKED (preflight): {exc}")
        return BLOCKED

    status = resp.get("status")
    detail = resp.get("detail", "")
    fingerprint = resp.get("fingerprint", "?")
    if status == "accepted":
        print(f"READY: {detail} (fingerprint {fingerprint})")
        return READY

    print(f"WARNING [{status}]: {detail} (fingerprint {fingerprint})")
    print(
        "  Run `chutes-cvm preflight` to submit this host class so Chutes can generate its "
        "measurements before you launch/upgrade."
    )
    return WARNING


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m chutes_cvm.guest.verify",
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
    parser.add_argument(
        "--config", metavar="PATH", help="Launch config.yaml with the miner hotkey."
    )
    parser.add_argument(
        "--api",
        metavar="URL",
        help="Validator base URL.",
        default="https://api.chutes.ai",
    )
    args = parser.parse_args()
    return verify_host(
        target_os=args.target_os, config_path=args.config, api_base=args.api
    )


if __name__ == "__main__":
    sys.exit(main())
