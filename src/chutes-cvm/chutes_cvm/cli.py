"""chutes-cvm — CLI for confidential-VM host operations.

Invoked as ``chutes-cvm <command>`` via the ``chutes-cvm`` console script (installed by the
package's ``src/chutes-cvm/install.sh``), or directly as ``python3 -m chutes_cvm.cli
<command>``.

This is the package-level dispatcher: it routes to the ``host`` group (setup / verify /
submit-profile / tune / restore), guest (``launch``, ``reset-gpus``), measurement
(``measurements``), and config (``config``) subpackages, so it lives at the package root
rather than under any one of them.

Stdlib-only dispatcher. Subcommands import their implementation lazily, so a command
that needs extra dependencies never burdens one that doesn't. Commands that delegate to a
bundled shell entrypoint (``down``, ``reset-gpus``) shell out to ``chutes_cvm/scripts/`` via
``_run_script``; the rest dispatch to a Python ``main`` in this package.
"""

import argparse
import os
import subprocess
import sys

from chutes_cvm.paths import SCRIPTS_DIR as _SCRIPTS_DIR
from chutes_cvm.paths import default_config_path

# _SCRIPTS_DIR is the package's bundled shell scripts (chutes_cvm/scripts/): the privileged
# volume/network helpers the Python launch orchestrator calls, plus teardown, discover-profile
# (used by the host verify/submit flow), reset-gpus. _run_script execs one; they travel with the
# package, so no host-tools on disk.


def _run_script(name: str, argv: "list[str]", cwd: "str | None" = None) -> int:
    """Exec a bundled chutes_cvm/scripts/<name> shell entrypoint, forwarding argv.

    ``cwd`` sets the working directory — a helper that calls sibling ``./volumes/`` /
    ``./network/`` scripts needs it set to the scripts dir so those resolve."""
    script = _SCRIPTS_DIR / name
    if not script.exists():
        print(f"chutes-cvm: {name} not found at {script}", file=sys.stderr)
        return 1
    return subprocess.call(["bash", str(script), *argv], cwd=cwd)


def _cmd_reset_gpus(args: argparse.Namespace) -> int:
    """Reset all GPUs via nvidia-gpu-tools SBR (delegates to devices/reset-gpus.sh)."""
    return _run_script("devices/reset-gpus.sh", [])


def _cmd_vfio_wedged(args: argparse.Namespace) -> int:
    """Exit 0 if host PCI passthrough operations are wedged (a reset is needed before
    launch), else 1. Lets orchestration gate a launch/reset on the machine-parseable code.
    """
    from chutes_cvm.guest.vfio import pci_operations_wedged

    return 0 if pci_operations_wedged() else 1


def _cmd_stop(args: argparse.Namespace) -> int:
    """Stop the running TDX VM only — leaves the bridge and volumes in place."""
    from chutes_cvm.guest.__main__ import stop_existing_vm

    stop_existing_vm()
    return 0


def _cmd_down(args: argparse.Namespace) -> int:
    """Bring the VM environment down. By default asks the guest to power off gracefully via the
    system-manager API (miner hotkey from config); --force force-kills QEMU instead. Either way,
    the host-side bridge + benchmark-netlog are then torn down.

    Network values come from config (resolved in Python, passed to teardown.sh as flags — no
    `chutes-cvm config` eval round-trip); teardown falls back to its own defaults if config is
    absent/unreadable.
    """
    from chutes_cvm.guest.config import ConfigError, LaunchConfig

    config = args.config or default_config_path()
    net_flags: "list[str]" = []
    cfg_ok = bool(config and os.path.exists(config))
    if cfg_ok:
        try:
            flat = LaunchConfig.from_file(config).flat()
            net_flags = [
                "--bridge-ip",
                flat["bridge_ip"],
                "--vm-ip",
                flat["vm_ip"],
                "--public-iface",
                flat["public_iface"],
            ]
        except ConfigError as exc:
            cfg_ok = False
            print(
                f"chutes-cvm: could not read {config} ({exc}); using defaults.",
                file=sys.stderr,
            )

    if not args.force:
        from chutes_cvm.guest.shutdown import ShutdownError, graceful_shutdown

        try:
            graceful_shutdown(config if cfg_ok else None)
        except ShutdownError as exc:
            print(
                f"chutes-cvm: graceful shutdown failed — {exc}\n"
                "  Run `chutes-cvm down --force` to force-kill the VM instead.",
                file=sys.stderr,
            )
            return 1
        # Guest is powering off on its own; teardown waits for it (no force-kill), then cleans up.
        return _run_script(
            "teardown.sh", net_flags + ["--no-stop"], cwd=str(_SCRIPTS_DIR)
        )
    return _run_script("teardown.sh", net_flags, cwd=str(_SCRIPTS_DIR))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chutes-cvm",
        description="Operate and inspect Chutes confidential GPU VMs on this host.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

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
        "host",
        add_help=False,
        help="Host lifecycle + attestation — setup / verify / submit-profile / tune / restore "
        "(args forwarded; `chutes-cvm host --help`).",
    )

    stop = sub.add_parser(
        "stop",
        help="Stop the running TDX VM only (leaves the bridge and volumes in place).",
    )
    stop.set_defaults(func=_cmd_stop)

    down = sub.add_parser(
        "down",
        help="Gracefully shut down the VM (via the guest API) and tear down its bridge + "
        "benchmark-netlog; --force force-kills QEMU instead.",
    )
    down.add_argument(
        "--config",
        metavar="PATH",
        help="config.yaml providing the miner hotkey (to sign the shutdown) and network "
        "values for bridge cleanup (default: ./config.yaml).",
    )
    down.add_argument(
        "--force",
        action="store_true",
        help="Force-kill QEMU instead of asking the guest to power off gracefully.",
    )
    down.set_defaults(func=_cmd_down)

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
        "image",
        add_help=False,
        help="Base image sets — download / verify / manifest (args forwarded; "
        "`chutes-cvm image --help`).",
    )
    sub.add_parser(
        "config",
        add_help=False,
        help="Manage the launch config.yaml — init / verify (args forwarded; "
        "`chutes-cvm config --help`).",
    )
    sub.add_parser(
        "measurements",
        add_help=False,
        help="Offline TDX measurement generation — generate / list "
        "(build-host tool; args forwarded, `chutes-cvm measurements --help`).",
    )

    return parser


# Commands whose arguments are forwarded verbatim to an underlying main(argv). Intercepted
# before argparse because REMAINDER mishandles leading options (e.g. `launch-vm --image`,
# `host --help`). Each underlying main owns its own --help. `launch-vm` is the hidden
# QEMU primitive (no visible subparser); `launch` is the Python end-to-end orchestrator
# (chutes_cvm.guest.launch) that drives the bash volume/network helpers then calls launch-vm.
_PASSTHROUGH = (
    "launch",
    "launch-vm",
    "host",
    "image",
    "config",
    "measurements",
)


def main(argv: "list[str] | None" = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in _PASSTHROUGH:
        forward = raw[1:]
        if raw[0] == "launch":
            # The end-to-end orchestrator is Python (decisions/precedence/validation/gates);
            # it invokes the bundled bash helpers for the privileged volume/network steps and
            # finally boots via the launch-vm primitive below.
            from chutes_cvm.guest.launch import main as _launch_orchestrator

            return _launch_orchestrator(forward)
        if raw[0] == "launch-vm":
            from chutes_cvm.guest.__main__ import main as _launch_main

            return _launch_main(forward)
        if raw[0] == "host":
            from chutes_cvm.host.cli import main as _host_main

            return _host_main(forward)
        if raw[0] == "image":
            from chutes_cvm.guest.image_set import main as _image_main

            return _image_main(forward)
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
