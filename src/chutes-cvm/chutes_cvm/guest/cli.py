"""chutes-cvm — CLI for confidential-VM host operations.

Invoked as ``chutes-cvm <command>`` via the shim installed by
``host-tools/provision/setup-chutes-cvm.sh`` (which runs ``python3 -m chutes_cvm.guest.cli``),
or directly as ``python3 -m chutes_cvm.guest.cli <command>``.

Stdlib-only dispatcher. Subcommands import their implementation lazily, so a command
that needs extra dependencies never burdens one that doesn't (``verify-host`` is pure
stdlib). New commands (launch, create-config, discover-profile, …) slot in via
``build_parser`` as the CLI grows to subsume the host-tools bash scripts.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# host-tools/scripts/ — the dir holding the shell entrypoints the CLI delegates to
# (cli.py lives at .../scripts/chutes/guest/cli.py). Kept as the front door while the
# implementations stay in their current form; individual commands get ported to modules
# over time without changing their CLI interface.
# Transitional: two commands still delegate to bash under the repo's host-tools/scripts
# (discover-profile.sh, devices/reset-gpus.sh). Resolve them relative to the repo root when
# running from a source checkout; override with CHUTES_CVM_SCRIPTS_DIR when the package is
# pip-installed and host-tools isn't on disk. TODO: port these to Python and drop _run_script.
_SCRIPTS_DIR = Path(
    os.environ.get("CHUTES_CVM_SCRIPTS_DIR")
    or (Path(__file__).resolve().parents[4] / "host-tools" / "scripts")
)

# verify_host's exit codes → (banner label, ANSI attributes). Kept here so the CLI owns
# presentation while chutes_cvm.guest.verify stays a plain int-returning gate.
_VERIFY_STATUS = {
    0: ("READY", "1;32"),  # bold green
    1: ("BLOCKED", "1;31"),  # bold red
    2: ("WARNING", "1;33"),  # bold yellow
}


def _run_script(name: str, argv: "list[str]") -> int:
    """Exec a host-tools/scripts/<name> shell entrypoint, forwarding argv."""
    script = _SCRIPTS_DIR / name
    if not script.exists():
        print(f"chutes-cvm: {name} not found at {script}", file=sys.stderr)
        return 1
    return subprocess.call(["bash", str(script), *argv])


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
    rc = verify_host(target_os=args.target_os)
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
        help="Launch the TDX guest VM (args forwarded; `chutes-cvm launch --help`).",
    )
    sub.add_parser(
        "setup-host",
        add_help=False,
        help="Set up this TDX host (args forwarded; `chutes-cvm setup-host --help`).",
    )

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

    return parser


# Commands whose arguments are forwarded verbatim to an underlying main(argv). Intercepted
# before argparse because REMAINDER mishandles leading options (e.g. `launch --image`,
# `setup-host --help`). Each underlying main owns its own --help.
_PASSTHROUGH = ("launch", "setup-host", "image-set", "config")


def main(argv: "list[str] | None" = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in _PASSTHROUGH:
        forward = raw[1:]
        if raw[0] == "launch":
            from chutes_cvm.guest.__main__ import main as _launch_main

            return _launch_main(forward)
        if raw[0] == "setup-host":
            from chutes_cvm.host.setup import main as _setup_main

            return _setup_main(forward)
        if raw[0] == "image-set":
            from chutes_cvm.guest.image_set import main as _image_set_main

            return _image_set_main(forward)
        from chutes_cvm.guest.config import main as _config_main

        return _config_main(forward)
    args = build_parser().parse_args(raw)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
