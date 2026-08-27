"""Run the launch gates without launching a VM — use before an upgrade to confirm
a node will relaunch and re-attest rather than going offline.

    python3 -m chutes_cvm.guest.verify              # relaunch as-is?
    python3 -m chutes_cvm.guest.verify --target-os 26.04   # ... after an OS upgrade?
    python3 -m chutes_cvm.guest.verify --submit     # ... and register an unmeasured host

Two gates: (A) the host runs the QEMU its OS release baselines (local), and (B) the control plane
has a published measurement for THIS image's (version, rc) that covers this host class — the API
check: read the image's (version, rc) from its manifest, capture + sign the host's platform
metadata, and ask POST /servers/tdx/preflight (the API owns the fingerprint and verdict). Gate B
is read-only; `--submit` additionally registers the host class (POST /servers/tdx/host_profiles)
so Chutes can generate its measurements (the miner's baselining path — no separate verb).

Exit: 0 READY · 1 BLOCKED (won't relaunch: wrong QEMU, unreadable image, or the check couldn't
run) · 2 WARNING (gates run, but no published measurement for this image x host yet).
"""

import argparse
import os
import sys

from chutes_cvm.guest import image_set
from chutes_cvm.guest.detection import SUPPORTED_QEMU_BY_OS, verify_host_qemu_supported
from chutes_cvm.guest.preflight import (
    DEFAULT_API_BASE,
    PreflightError,
    run_preflight,
    submit_profile,
)
from chutes_cvm.paths import SCRIPTS_DIR, default_config_path

READY = 0
BLOCKED = 1
WARNING = 2


def _image_version_rc(config_path: str, base_image: "str | None") -> "tuple[str, bool]":
    """Resolve the base image the host would relaunch and read its ``(version, rc)`` from the
    manifest. ``base_image`` overrides; otherwise the config's ``vm.base_image``, else the
    production default. Raises ValueError/OSError if no manifest can be read."""
    base = base_image
    if not base:
        try:
            from chutes_cvm.guest.config import LaunchConfig

            base = LaunchConfig.from_file(
                config_path if config_path and os.path.exists(config_path) else None
            ).vm.base_image
        except Exception:
            # Config is optional for a bare `verify`; fall back to the default set.
            base = ""
    return image_set.version_and_rc(base or image_set.DEFAULT_BASE_IMAGE)


def verify_host(
    target_os: "str | None" = None,
    scripts_dir: "str | None" = None,
    config_path: "str | None" = None,
    api_base: "str | None" = None,
    submit: bool = False,
    base_image: "str | None" = None,
) -> int:
    """Run the launch gates without launching; return one of READY/BLOCKED/WARNING.

    ``submit`` also registers an unmeasured host class (POST /servers/tdx/host_profiles) so Chutes
    can generate its measurements — on top of the read-only Gate B check.
    """
    scripts_dir = scripts_dir or str(SCRIPTS_DIR)

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

    # Gate B: does a published measurement for the image this host would relaunch — its
    # (version, rc) — cover this host class? Read the image's (version, rc) from its manifest,
    # capture + sign the host profile, and ask the API (which owns the fingerprint and verdict).
    config = config_path or default_config_path()
    api = api_base or os.environ.get("CHUTES_API_BASE") or DEFAULT_API_BASE
    try:
        version, rc = _image_version_rc(config, base_image)
    except (FileNotFoundError, ValueError, OSError) as exc:
        # Can't determine what would boot -> can't check it. Fail closed.
        print(f"BLOCKED (image): {exc}")
        return BLOCKED
    label = f"{version}{' (rc)' if rc else ''}"

    try:
        resp = run_preflight(
            config_path=config,
            scripts_dir=scripts_dir,
            version=version,
            rc=rc,
            api_base=api,
            target_qemu=target_qemu,
        )
    except PreflightError as exc:
        # Fail closed: if we cannot get a verdict, the host would attest into the unknown.
        print(f"BLOCKED (API check): {exc}")
        return BLOCKED

    detail = resp.get("detail", "")
    fingerprint = resp.get("fingerprint", "?")
    if resp.get("launchable"):
        print(f"READY: {detail} (fingerprint {fingerprint})")
        return READY

    print(f"WARNING: cannot attest {label} yet — {detail} (fingerprint {fingerprint})")
    if submit:
        try:
            sub = submit_profile(
                config_path=config, scripts_dir=scripts_dir, api_base=api
            )
        except PreflightError as exc:
            print(f"  Registration failed: {exc}")
            return WARNING
        already = "" if sub.get("stored") else " (already on file)"
        print(
            f"  Registered this host class for measurement{already} — Chutes will generate its "
            "measurements; re-check readiness later."
        )
    else:
        print(
            "  Run `chutes-cvm host submit-profile` (or re-run with --submit) to register this "
            "host class so Chutes can generate its measurements before you launch/upgrade."
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
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Also register this host class with Chutes if it is not yet measured "
        "(POST /servers/tdx/host_profiles), on top of the read-only check.",
    )
    parser.add_argument(
        "--base-image",
        metavar="DIR",
        help="Base image set to check (default: the config's vm.base_image, else the "
        "production set). Its manifest gives the (version, rc) the check joins against.",
    )
    args = parser.parse_args()
    return verify_host(
        target_os=args.target_os,
        config_path=args.config,
        api_base=args.api,
        submit=args.submit,
        base_image=args.base_image,
    )


if __name__ == "__main__":
    sys.exit(main())
