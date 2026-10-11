"""Run the launch gates without launching a VM — use before an upgrade to confirm
a node will relaunch and re-attest rather than going offline.

    python3 -m chutes_cvm.guest.verify              # relaunch as-is?
    python3 -m chutes_cvm.guest.verify --target-os 26.04   # ... after an OS upgrade?
    python3 -m chutes_cvm.guest.verify --submit     # ... and register an unmeasured host

Two gates: (A) the host runs the QEMU its OS release baselines (local), and (B) the control plane
knows this host class and has published measurements for it — capture + sign the host's platform
metadata and fetch its class (``HostClass.fetch``; the API owns the fingerprint and verdict).

Gate B is deliberately VERSION-FREE. A host is verified before it has downloaded any image, so the
question here is "can this box run anything, and what" — never "does version X work". Whether one
specific image can boot is `guest launch`'s preflight, which reads the (version, rc) it actually
holds. Gate B is read-only; `--submit` additionally registers the host class
(POST /servers/tdx/host_profiles) so Chutes can generate its measurements (the miner's baselining
path — no separate verb).

If a base image happens to be downloaded, its (version, rc) is checked against the covered set as a
NOTE — flagged, never fatal, since a missing measurement for one image says nothing about whether
the host class is viable.

Exit: 0 READY · 1 BLOCKED (won't relaunch: wrong QEMU, or the check couldn't run) · 2 WARNING
(gates run, but nothing published for this host class yet, or the local image is not covered).
"""

import argparse
import os
import sys

from chutes_cvm.guest import image_set
from chutes_cvm.guest.chutes_api import DEFAULT_API_BASE, ChutesApiError, submit_profile
from chutes_cvm.guest.config import LaunchConfig
from chutes_cvm.guest.detection import SUPPORTED_QEMU_BY_OS, verify_host_qemu_supported
from chutes_cvm.guest.host_class import HostClass, HostClassStatus, NotMeasured
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.image_set import ImageSet
from chutes_cvm.paths import default_config_path

READY = 0
BLOCKED = 1
WARNING = 2


def _image_set(config_path: str, image_set_dir: "str | None") -> ImageSet:
    """Resolve the base image set the host would relaunch and read it from its
    manifest. ``image_set_dir`` overrides; otherwise the config's ``vm.base_image``, else the
    production default. Raises ValueError/OSError if no manifest can be read.

    Only for the informational note — verification never depends on an image being present.
    """
    base = image_set_dir
    if not base:
        try:
            base = LaunchConfig.from_file(
                config_path if config_path and os.path.exists(config_path) else None
            ).vm.base_image
        except Exception:
            # Config is optional for a bare `verify`; fall back to the default set.
            base = ""
    return ImageSet.from_dir(base or image_set.DEFAULT_IMAGE_SET_DIR)


def verify_host(
    target_os: "str | None" = None,
    config_path: "str | None" = None,
    api_base: "str | None" = None,
    submit: bool = False,
    image_set_dir: "str | None" = None,
) -> int:
    """Run the launch gates without launching; return one of READY/BLOCKED/WARNING.

    ``submit`` also registers an unmeasured host class (POST /servers/tdx/host_profiles) so Chutes
    can generate its measurements — on top of the read-only Gate B check.
    """

    # Gate A: which QEMU's measurement matters?
    if target_os is None:
        # As-is: the host must be on the QEMU its current OS ships.
        try:
            verify_host_qemu_supported()
        except ValueError as exc:
            print(f"BLOCKED (QEMU): {exc}")
            return BLOCKED
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
        print(
            f"Checking against target OS {target_os} (ships QEMU {expected}); "
            f"the live QEMU is ignored because the upgrade replaces it."
        )

    # Gate B: is this host class known, and which images cover it? Capture + sign the host
    # profile and ask the API (which owns the fingerprint and verdict). No version is sent — a
    # host is verified before it has downloaded anything, so the answer must not depend on disk.
    config = config_path or default_config_path()
    api = api_base or os.environ.get("CHUTES_API_BASE") or DEFAULT_API_BASE

    # One reading of the host: the class lookup and any registration below describe the same one.
    try:
        host = HostProfile.from_host()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"BLOCKED (host): cannot read this host: {exc}")
        return BLOCKED
    try:
        host_class = HostClass.fetch(
            host, config_path=config, api_base=api, target_os=target_os
        )
    except ChutesApiError as exc:
        # Fail closed: if we cannot get a verdict, the host would attest into the unknown.
        print(f"BLOCKED (API check): {exc}")
        return BLOCKED

    fingerprint = host_class.fingerprint
    if host_class.measured:
        print(f"READY: {_covered_label(host_class)} (fingerprint {fingerprint})")
        return _note_local_image(config, image_set_dir, host_class)

    print(f"WARNING: {host_class.detail} (fingerprint {fingerprint})")
    if host_class.status is HostClassStatus.PENDING:
        # Already on file — re-submitting neither helps nor advances the queue, so don't offer it.
        print(
            "  Nothing to do: the class is registered and awaiting measurement generation."
        )
        return WARNING
    if submit:
        try:
            sub = submit_profile(
                config_path=config,
                host_profile=host,
                api_base=api,
                target_os=target_os,
            )
        except ChutesApiError as exc:
            print(f"  Registration failed: {exc}")
            return WARNING
        already = "" if sub.get("stored") else " (already on file)"
        print(
            f"  Registered this host class for measurement{already} — Chutes will generate its "
            "measurements; re-check readiness later."
        )
    else:
        flag = f" --target-os {target_os}" if target_os else ""
        print(
            f"  Run `chutes-cvm host submit-profile{flag}` (or re-run with --submit) to register "
            "this host class so Chutes can generate its measurements before you launch/upgrade."
        )
    return WARNING


def _covered_label(host_class: HostClass) -> str:
    """The READY line: which published images this host class can launch."""
    images = ", ".join(entry.label for entry in host_class.measured)
    return f"this host class is measured and can launch: {images}"


def _note_local_image(
    config_path: str, image_set_dir: "str | None", host_class: HostClass
) -> int:
    """Flag whether a DOWNLOADED base image is in the covered set — informational only.

    The host class is already verified by the time this runs, so a missing image, missing manifest,
    or uncovered version never invalidates that; it only tells the operator the specific image they
    hold would not attest, which `guest launch` would refuse anyway. No image on disk is the normal
    case for a freshly verified host, and returns READY untouched.
    """
    try:
        image = _image_set(config_path, image_set_dir)
    except (FileNotFoundError, ValueError, OSError):
        # Nothing downloaded yet (or no manifest) — expected before `chutes-cvm image download`.
        return READY
    try:
        host_class.measured_image(image)
    except NotMeasured:
        pass
    else:
        print(f"  Downloaded image {image.label} is covered.")
        return READY
    print(
        f"  NOTE: the downloaded image {image.label} is NOT in the covered set — this host class can "
        "launch the images listed above, but not that one. Download a covered image, or wait for "
        "its measurement to be published."
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
        help="Base image set for the informational note (default: the config's vm.base_image, "
        "else the production set). Verification itself is version-free and needs no image.",
    )
    args = parser.parse_args()
    return verify_host(
        target_os=args.target_os,
        config_path=args.config,
        api_base=args.api,
        submit=args.submit,
        image_set_dir=args.base_image,
    )


if __name__ == "__main__":
    sys.exit(main())
