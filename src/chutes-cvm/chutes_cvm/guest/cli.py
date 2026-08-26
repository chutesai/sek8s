"""``chutes-cvm guest <verb>`` — TDX VM runtime lifecycle.

Groups the guest-VM operator verbs under one noun, mirroring the ``host`` group and matching
the CLI's noun/verb pattern. Dispatched via the top-level ``guest`` passthrough in
``chutes_cvm.cli``.

  chutes-cvm guest launch       # bring a VM up end-to-end from config.yaml (args forwarded)
  chutes-cvm guest stop         # stop the running VM only (leave bridge + volumes in place)
  chutes-cvm guest down         # graceful shutdown via the guest API + bridge teardown (--force)

GPU/PCI hardware ops (`reset-gpus`, `vfio-wedged`) live under the ``host`` noun: they act on
host hardware and are useful with or without a running guest. The low-level QEMU-boot primitive
(``chutes_cvm.guest.__main__``) is not a CLI command either — ``guest launch`` reaches it via a
Python import, not the CLI.
"""

from __future__ import annotations

import argparse
import os
import sys

from chutes_cvm import proc
from chutes_cvm.paths import SCRIPTS_DIR, default_config_path


def _run_script(name: str, argv: "list[str]", cwd: "str | None" = None) -> int:
    """Exec a bundled chutes_cvm/scripts/<name> shell entrypoint, forwarding argv.

    ``cwd`` sets the working directory — a helper that calls sibling ``./volumes/`` /
    ``./network/`` scripts needs it set to the scripts dir so those resolve."""
    script = SCRIPTS_DIR / name
    if not script.exists():
        print(f"chutes-cvm: {name} not found at {script}", file=sys.stderr)
        return 1
    return proc.call(["bash", str(script), *argv], cwd=cwd)


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
                "  Run `chutes-cvm guest down --force` to force-kill the VM instead.",
                file=sys.stderr,
            )
            return 1
        # Guest is powering off on its own; teardown waits for it (no force-kill), then cleans up.
        return _run_script(
            "teardown.sh", net_flags + ["--no-stop"], cwd=str(SCRIPTS_DIR)
        )
    return _run_script("teardown.sh", net_flags, cwd=str(SCRIPTS_DIR))


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # `launch` owns its own argparse (--config-volume / --benchmark / --foreground / ...), so
    # forward to it verbatim before our argparse touches the args (matches host.cli's `setup`
    # forward and the top-level passthrough pattern). The end-to-end orchestrator is Python.
    if argv and argv[0] == "launch":
        from chutes_cvm.guest.launch import main as _launch_orchestrator

        return _launch_orchestrator(argv[1:])

    parser = argparse.ArgumentParser(
        prog="chutes-cvm guest", description="TDX VM runtime lifecycle."
    )
    sub = parser.add_subparsers(dest="verb", required=True, metavar="<verb>")

    # Registered for `chutes-cvm guest --help` visibility; dispatched above.
    sub.add_parser(
        "launch",
        add_help=False,
        help="Launch a VM end-to-end from config.yaml — volumes, network, then boot "
        "(args forwarded; `chutes-cvm guest launch --help`).",
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
