"""chutes-cvm — CLI for confidential-VM host operations.

Invoked as ``chutes-cvm <command>`` via the ``chutes-cvm`` console script (installed by the
package's ``src/chutes-cvm/install.sh``), or directly as ``python3 -m chutes_cvm.cli
<command>``.

This is the package-level dispatcher: it routes to host (``setup-host``, ``tune-host``),
guest (``launch``, ``reset-gpus``, ``preflight``), and measurement (``measurements``)
subpackages, so it lives at the package root rather than under any one of them.

Stdlib-only dispatcher. Subcommands import their implementation lazily, so a command
that needs extra dependencies never burdens one that doesn't (``verify-host`` is pure
stdlib). Commands that delegate to a bundled shell entrypoint (``launch``, ``discover-profile``,
``reset-gpus``) shell out to ``chutes_cvm/scripts/`` via ``_run_script``; the rest dispatch
to a Python ``main`` in this package.
"""

import argparse
import os
import subprocess
import sys

from chutes_cvm.paths import SCRIPTS_DIR as _SCRIPTS_DIR
from chutes_cvm.paths import default_config_path

# _SCRIPTS_DIR is the package's bundled shell scripts (chutes_cvm/scripts/): the VM-launch
# orchestrator (quick-launch → `up`), volumes/, network/, discover-profile, reset-gpus.
# _run_script execs one of them; they travel with the package, so no host-tools on disk.

# verify_host's exit codes → (banner label, ANSI attributes). Kept here so the CLI owns
# presentation while chutes_cvm.guest.verify stays a plain int-returning gate.
_VERIFY_STATUS = {
    0: ("READY", "1;32"),  # bold green
    1: ("BLOCKED", "1;31"),  # bold red
    2: ("WARNING", "1;33"),  # bold yellow
}


def _run_script(name: str, argv: "list[str]", cwd: "str | None" = None) -> int:
    """Exec a bundled chutes_cvm/scripts/<name> shell entrypoint, forwarding argv.

    ``cwd`` sets the working directory — the orchestrator (quick-launch.sh) needs it set to
    the scripts dir so its sibling ``./volumes/`` / ``./network/`` calls resolve."""
    script = _SCRIPTS_DIR / name
    if not script.exists():
        print(f"chutes-cvm: {name} not found at {script}", file=sys.stderr)
        return 1
    return subprocess.call(["bash", str(script), *argv], cwd=cwd)


def _color(text: str, attrs: str) -> str:
    """Wrap ``text`` in an ANSI attribute string, unless output isn't a TTY or NO_COLOR
    is set (so piped/redirected output and dumb terminals stay clean)."""
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{attrs}m{text}\033[0m"


def _cmd_verify_host(args: argparse.Namespace) -> int:
    """Run the host-readiness gates and print a colored result banner."""
    from chutes_cvm.guest.verify import verify_host

    print(_color("── chutes-cvm: host verification ──", "1;36"))
    rc = verify_host(
        target_os=args.target_os,
        scripts_dir=str(_SCRIPTS_DIR),
        config_path=args.config,
        api_base=args.api,
    )
    label, attrs = _VERIFY_STATUS.get(rc, (f"EXIT {rc}", "1"))
    print(_color(f"\nResult: {label}", attrs))
    return rc


def _cmd_discover_profile(args: argparse.Namespace) -> int:
    """Capture this host's GPU/CPU/NUMA profile (delegates to discover-profile.sh)."""
    forwarded = []
    if args.json_only:
        forwarded.append("--json-only")
    if args.no_json:
        forwarded.append("--no-json")
    return _run_script("discover-profile.sh", forwarded)


def _cmd_tune_host(args: argparse.Namespace) -> int:
    """Apply NVIDIA-recommended host CPU tuning."""
    from chutes_cvm.host.tune import apply_tuning

    apply_tuning()
    return 0


def _cmd_restore_host(args: argparse.Namespace) -> int:
    """Restore host CPU settings saved by tune-host."""
    from chutes_cvm.host.tune import restore_tuning

    restore_tuning()
    return 0


def _cmd_reset_gpus(args: argparse.Namespace) -> int:
    """Reset all GPUs via nvidia-gpu-tools SBR (delegates to devices/reset-gpus.sh)."""
    return _run_script("devices/reset-gpus.sh", [])


def _cmd_vfio_wedged(args: argparse.Namespace) -> int:
    """Exit 0 if host PCI passthrough operations are wedged (a reset is needed before
    launch), else 1. Lets orchestration gate a launch/reset on the machine-parseable code.
    """
    from chutes_cvm.guest.vfio import pci_operations_wedged

    return 0 if pci_operations_wedged() else 1


def _cmd_preflight(args: argparse.Namespace) -> int:
    """Ask the control plane whether this host class can launch (submits it if unknown)."""
    from chutes_cvm.guest.preflight import (
        DEFAULT_API_BASE,
        FAIL_CLOSED,
        PreflightError,
        run_preflight,
        status_exit_code,
    )

    config = args.config or default_config_path()
    api = args.api or os.environ.get("CHUTES_API_BASE") or DEFAULT_API_BASE
    try:
        resp = run_preflight(
            config_path=config,
            scripts_dir=str(_SCRIPTS_DIR),
            api_base=api,
            dry_run=args.dry_run,
        )
    except PreflightError as exc:
        print(_color(f"PREFLIGHT FAILED (refusing to launch): {exc}", "1;31"))
        return FAIL_CLOSED

    status = resp.get("status")
    attrs = {"accepted": "1;32", "pending": "1;33"}.get(status, "1;33")
    print(_color(f"[{status}] {resp.get('detail', '')}", attrs))
    print(f"  fingerprint: {resp.get('fingerprint', '?')}")
    return status_exit_code(status)


def _cmd_download(args: argparse.Namespace) -> int:
    """Download + manifest-verify a base image set (production, or debug with --debug)."""
    base = "tdx-guest-debug" if args.debug else "tdx-guest"
    return _run_script("download-image-set.sh", [base])


def _cmd_init(args: argparse.Namespace) -> int:
    """Write a starter config.yaml (from the bundled template) into the current directory."""
    import shutil

    template = _SCRIPTS_DIR / "config" / "config.tmpl.yaml"
    dest = "config.yaml"
    if os.path.exists(dest) and not args.force:
        print(
            f"chutes-cvm: {dest} already exists — pass --force to overwrite.",
            file=sys.stderr,
        )
        return 1
    shutil.copyfile(template, dest)
    print(f"Created {dest} (from {template.name}). Edit it, then `chutes-cvm launch`.")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    """Stop the running TDX VM only — leaves the bridge and volumes in place."""
    from chutes_cvm.guest.__main__ import stop_existing_vm

    stop_existing_vm()
    return 0


def _cmd_down(args: argparse.Namespace) -> int:
    """Full teardown: stop the VM and tear down its bridge + benchmark-netlog service."""
    config = args.config or default_config_path()
    forward = [config] if os.path.exists(config) else []
    return _run_script("teardown.sh", forward, cwd=str(_SCRIPTS_DIR))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chutes-cvm",
        description="Operate and inspect Chutes confidential GPU VMs on this host.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    verify = sub.add_parser(
        "verify-host",
        help="Check this host will relaunch and re-attest (optionally after an OS upgrade).",
        description=(
            "Run the launch gates without launching a VM: host QEMU is the one its OS "
            "release baselines, and the host topology resolves to a baselined fingerprint. "
            "Exit 0 READY / 1 BLOCKED / 2 WARNING."
        ),
    )
    verify.add_argument(
        "--target-os",
        metavar="VERSION",
        help="Verify against a target OS release's QEMU (pre-upgrade check), e.g. 26.04.",
    )
    verify.add_argument(
        "--config",
        metavar="PATH",
        help="Launch config.yaml with the miner hotkey (default: ./config.yaml; env CHUTES_CVM_CONFIG).",
    )
    verify.add_argument(
        "--api",
        metavar="URL",
        help="Control-plane base URL (default: https://api.chutes.ai; env CHUTES_API_BASE).",
    )
    verify.set_defaults(func=_cmd_verify_host)

    discover = sub.add_parser(
        "discover-profile",
        help="Capture this host's GPU/CPU/NUMA profile as JSON (to baseline a new host class).",
        description=(
            "Probe the host's GPUs, CPU, NUMA and PCI topology and write a discover-profile "
            "JSON (plus a terminal report). Send that JSON to Chutes to baseline a new host "
            "class and generate its measurements."
        ),
    )
    output = discover.add_mutually_exclusive_group()
    output.add_argument(
        "--json-only",
        action="store_true",
        help="Write only the JSON file (skip the terminal report).",
    )
    output.add_argument(
        "--no-json",
        action="store_true",
        help="Print the terminal report only (skip the JSON file).",
    )
    discover.set_defaults(func=_cmd_discover_profile)

    # Pass-through commands (see _PASSTHROUGH / main): everything after the subcommand is
    # forwarded verbatim to the underlying launcher/setup, which own their own --help. These
    # entries exist for `chutes-cvm --help` visibility; main() intercepts them before argparse
    # (argparse REMAINDER mishandles leading options like --help/--image), so no func is set.
    sub.add_parser(
        "launch",
        add_help=False,
        help="Launch a VM end-to-end from config.yaml — volumes, network, then boot "
        "(args forwarded; `chutes-cvm launch --help`).",
    )
    # launch-vm is the low-level QEMU primitive: only the orchestrator (launch) and advanced
    # tooling (prime-vm) call it directly, so it is intentionally NOT registered as a visible
    # subcommand. main() still dispatches it via _PASSTHROUGH; `chutes-cvm launch-vm --help`
    # shows the primitive's own argparse.
    sub.add_parser(
        "setup-host",
        add_help=False,
        help="Set up this TDX host (args forwarded; `chutes-cvm setup-host --help`).",
    )

    download = sub.add_parser(
        "download",
        help="Download + verify a base image set into /var/lib/chutes/base-images/ (first-run step).",
        description=(
            "Fetch a published base image set (qcow2 + direct-boot artifacts + manifest) and "
            "verify every byte against the manifest. Run this once before the first launch."
        ),
    )
    download.add_argument(
        "--debug",
        action="store_true",
        help="Download the debug image set (SSH, no encryption) instead of production.",
    )
    download.set_defaults(func=_cmd_download)

    init = sub.add_parser(
        "init",
        help="Write a starter config.yaml into the current directory (edit it, then launch).",
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config.yaml.",
    )
    init.set_defaults(func=_cmd_init)

    stop = sub.add_parser(
        "stop",
        help="Stop the running TDX VM only (leaves the bridge and volumes in place).",
    )
    stop.set_defaults(func=_cmd_stop)

    down = sub.add_parser(
        "down",
        help="Full teardown: stop the VM and tear down its bridge + benchmark-netlog service.",
    )
    down.add_argument(
        "--config",
        metavar="PATH",
        help="config.yaml whose network values drive bridge cleanup "
        "(default: ./config.yaml).",
    )
    down.set_defaults(func=_cmd_down)

    tune = sub.add_parser(
        "tune-host",
        help="Apply NVIDIA-recommended host CPU tuning (performance governor, no C1E/C6).",
    )
    tune.set_defaults(func=_cmd_tune_host)

    restore = sub.add_parser(
        "restore-host",
        help="Restore host CPU settings saved by tune-host (no-op if never tuned).",
    )
    restore.set_defaults(func=_cmd_restore_host)

    reset = sub.add_parser(
        "reset-gpus",
        help="Reset all GPUs via nvidia-gpu-tools SBR (stop the VM first).",
    )
    reset.set_defaults(func=_cmd_reset_gpus)

    vfio = sub.add_parser(
        "vfio-wedged",
        help="Exit 0 if host PCI passthrough is wedged and needs a reset before launch, else 1.",
    )
    vfio.set_defaults(func=_cmd_vfio_wedged)

    pre = sub.add_parser(
        "preflight",
        help="Ask Chutes whether this host class can launch (submits it if unknown).",
        description=(
            "Capture this host's platform metadata, sign it with the miner hotkey, and POST it "
            "to the control plane, which returns a status: accepted (can launch), pending "
            "(submitted, awaiting measurements), or unknown (dry-run only). "
            "Exit 0 accepted / 1 error (fail-closed) / 2 not-yet."
        ),
    )
    pre.add_argument(
        "--config",
        metavar="PATH",
        help="Launch config.yaml with the miner hotkey (default: ./config.yaml; env CHUTES_CVM_CONFIG).",
    )
    pre.add_argument(
        "--api",
        metavar="URL",
        help="Control-plane base URL (default: https://api.chutes.ai; env CHUTES_API_BASE).",
    )
    pre.add_argument(
        "--dry-run",
        action="store_true",
        help="Report status without submitting the profile when unknown.",
    )
    pre.set_defaults(func=_cmd_preflight)

    # Pass-through modules with their own argparse (see _PASSTHROUGH / main).
    sub.add_parser(
        "image-set",
        add_help=False,
        help="Build/verify a base image-set manifest (args forwarded; `chutes-cvm image-set --help`).",
    )
    sub.add_parser(
        "config",
        add_help=False,
        help="Render/validate a config.yaml to KEY=value env (args forwarded).",
    )
    sub.add_parser(
        "measurements",
        add_help=False,
        help="Offline TDX measurement generation — generate / list / selftest "
        "(build-host tool; args forwarded, `chutes-cvm measurements --help`).",
    )

    return parser


# Commands whose arguments are forwarded verbatim to an underlying main(argv). Intercepted
# before argparse because REMAINDER mishandles leading options (e.g. `launch-vm --image`,
# `setup-host --help`). Each underlying main owns its own --help. `launch-vm` is the hidden
# QEMU primitive (no visible subparser); `launch` is the end-to-end orchestrator.
_PASSTHROUGH = (
    "launch",
    "launch-vm",
    "setup-host",
    "image-set",
    "config",
    "measurements",
)


def main(argv: "list[str] | None" = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in _PASSTHROUGH:
        forward = raw[1:]
        if raw[0] == "launch":
            # The end-to-end orchestrator is a bundled shell script; run it from the
            # scripts dir so its ./volumes/ and ./network/ sibling calls resolve. Its final
            # step is `chutes-cvm launch-vm` (the primitive below).
            return _run_script("quick-launch.sh", forward, cwd=str(_SCRIPTS_DIR))
        if raw[0] == "launch-vm":
            from chutes_cvm.guest.__main__ import main as _launch_main

            return _launch_main(forward)
        if raw[0] == "setup-host":
            from chutes_cvm.host.setup import main as _setup_main

            return _setup_main(forward)
        if raw[0] == "image-set":
            from chutes_cvm.guest.image_set import main as _image_set_main

            return _image_set_main(forward)
        if raw[0] == "measurements":
            from chutes_cvm.measurement.generate_measurements import (
                main as _measurements_main,
            )

            return _measurements_main(forward)
        from chutes_cvm.guest.config import main as _config_main

        return _config_main(forward)
    args = build_parser().parse_args(raw)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
