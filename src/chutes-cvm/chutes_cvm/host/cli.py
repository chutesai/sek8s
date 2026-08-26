"""``chutes-cvm host <verb>`` — host lifecycle and attestation.

Groups the former setup-host / verify-host / tune-host / restore-host commands (and the new
submit-profile) under one noun, matching the CLI's noun/verb pattern. Dispatched via the
top-level ``host`` passthrough in ``chutes_cvm.cli``.

  chutes-cvm host setup            # provision this TDX host (args forwarded to host.setup)
  chutes-cvm host verify           # will this host relaunch + re-attest? (optionally --target-os)
  chutes-cvm host submit-profile   # register this host class with Chutes for baselining
  chutes-cvm host tune / restore   # NVIDIA host CPU tuning, and revert
"""

from __future__ import annotations

import argparse
import os
import sys

from chutes_cvm.paths import SCRIPTS_DIR

# verify/submit exit codes → (banner label, ANSI attributes).
_VERIFY_STATUS = {
    0: ("READY", "1;32"),  # bold green
    1: ("BLOCKED", "1;31"),  # bold red
    2: ("WARNING", "1;33"),  # bold yellow
}


def _color(text: str, attrs: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{attrs}m{text}\033[0m"


def _run_verify(target_os, config, api, *, submit: bool, banner: str) -> int:
    """Run the host gates (via chutes_cvm.guest.verify) and print a colored result banner."""
    from chutes_cvm.guest.verify import verify_host

    print(_color(f"── chutes-cvm: {banner} ──", "1;36"))
    rc = verify_host(
        target_os=target_os,
        scripts_dir=str(SCRIPTS_DIR),
        config_path=config,
        api_base=api,
        submit=submit,
    )
    label, attrs = _VERIFY_STATUS.get(rc, (f"EXIT {rc}", "1"))
    print(_color(f"\nResult: {label}", attrs))
    return rc


def _cmd_verify(args: argparse.Namespace) -> int:
    return _run_verify(
        args.target_os, args.config, args.api, submit=False, banner="host verification"
    )


def _cmd_submit_profile(args: argparse.Namespace) -> int:
    # Registration is the non-dry-run path of the same gate flow (was `verify-host --submit`).
    return _run_verify(
        None, args.config, args.api, submit=True, banner="host class submission"
    )


def _cmd_tune(args: argparse.Namespace) -> int:
    from chutes_cvm.host.tune import apply_tuning

    apply_tuning()
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    from chutes_cvm.host.tune import restore_tuning

    restore_tuning()
    return 0


def _add_api_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--config",
        metavar="PATH",
        help="Launch config.yaml with the miner hotkey (default: ./config.yaml; env CHUTES_CVM_CONFIG).",
    )
    p.add_argument(
        "--api",
        metavar="URL",
        help="Control-plane base URL (default: https://api.chutes.ai; env CHUTES_API_BASE).",
    )


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # `setup` owns its own argparse (--topology-matrix / --noninteractive), so forward to it
    # verbatim before our argparse touches the args (matches the top-level passthrough pattern).
    if argv and argv[0] == "setup":
        from chutes_cvm.host.setup import main as _setup_main

        return _setup_main(argv[1:])

    parser = argparse.ArgumentParser(
        prog="chutes-cvm host", description="Host lifecycle and attestation."
    )
    sub = parser.add_subparsers(dest="verb", required=True, metavar="<verb>")

    # Registered for `chutes-cvm host --help` visibility; dispatched above.
    sub.add_parser(
        "setup",
        add_help=False,
        help="Provision this TDX host (args forwarded; `chutes-cvm host setup --help`).",
    )

    verify = sub.add_parser(
        "verify",
        help="Check this host will relaunch and re-attest (optionally after an OS upgrade).",
        description=(
            "Run the launch gates without launching a VM: host QEMU is the one its OS release "
            "baselines, and the control plane has a published measurement for this host class. "
            "Exit 0 READY / 1 BLOCKED / 2 WARNING. To register an unbaselined host class, use "
            "`chutes-cvm host submit-profile`."
        ),
    )
    verify.add_argument(
        "--target-os",
        metavar="VERSION",
        help="Verify against a target OS release's QEMU (pre-upgrade check), e.g. 26.04.",
    )
    _add_api_args(verify)
    verify.set_defaults(func=_cmd_verify)

    submit = sub.add_parser(
        "submit-profile",
        help="Register this host class with Chutes so it can generate measurements.",
        description=(
            "Capture this host's platform metadata, sign it with the miner hotkey, and submit it "
            "to the control plane for baselining (the non-dry-run of `host verify`). Use when "
            "`host verify` reports the host class is not yet baselined."
        ),
    )
    _add_api_args(submit)
    submit.set_defaults(func=_cmd_submit_profile)

    tune = sub.add_parser(
        "tune",
        help="Apply NVIDIA-recommended host CPU tuning (governor=performance, disable C1E/C6).",
    )
    tune.set_defaults(func=_cmd_tune)

    restore = sub.add_parser(
        "restore",
        help="Restore host CPU settings saved by `host tune` (no-op if never tuned).",
    )
    restore.set_defaults(func=_cmd_restore)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
