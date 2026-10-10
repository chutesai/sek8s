"""chutes-cvm — CLI for confidential-VM host operations.

Invoked as ``chutes-cvm <command>`` via the ``chutes-cvm`` console script (installed by the
package's ``src/chutes-cvm/install.sh``), or directly as ``python3 -m chutes_cvm.cli
<command>``.

This is the package-level dispatcher: it routes to the ``guest`` group (launch / stop / down),
the ``host`` group (setup / verify / submit-profile / tune / restore / reset-gpus /
vfio-wedged), image (``image``), measurement (``measurements``), and config (``config``)
subpackages, so it lives at the package root rather than under any one of them.

Stdlib-only dispatcher. Every operator command is a noun group (guest / host / image / config /
measurements) whose args are forwarded verbatim to that subpackage's own ``main`` — each owns
its own ``--help`` and imports its implementation lazily, so a command that needs extra
dependencies never burdens one that doesn't. The low-level QEMU-boot primitive
(``chutes_cvm.guest.vm``) is not a CLI command — ``guest launch`` reaches it via import.
"""

import argparse
import os
import sys


def _installed_version() -> str:
    """The version of the installed ``chutes-cvm`` distribution (accurate for both the
    editable and non-editable install, since it reads the dist metadata)."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("chutes-cvm")
    except PackageNotFoundError:
        return "unknown"


def _cmd_version(_args: argparse.Namespace) -> int:
    """Print the version and where this CLI resolves from — the `package`/`python` lines let a
    miner spot a stray editable/.venv install shadowing the install.sh shim on PATH."""
    import chutes_cvm

    print(f"chutes-cvm {_installed_version()}")
    print(f"  package: {os.path.dirname(os.path.abspath(chutes_cvm.__file__))}")
    print(f"  python:  {sys.executable}")
    return 0


def _checkout_installer() -> "str | None":
    """The install.sh of the checkout this CLI is installed FROM, or None when it is not.

    An editable install leaves ``chutes_cvm`` inside ``<repo>/src/chutes-cvm/``; a non-editable
    one copies it into the venv's site-packages, where no sibling install.sh exists. That is the
    whole test — the same resolution `version` prints."""
    import chutes_cvm

    pkg_dir = os.path.dirname(os.path.abspath(chutes_cvm.__file__))
    installer = os.path.join(os.path.dirname(pkg_dir), "install.sh")
    return installer if os.path.isfile(installer) else None


def _cmd_update(args: argparse.Namespace) -> int:
    """Re-run install.sh to update this CLI in place.

    install.sh stays the single source of truth for installing; this only picks the mode and
    hands off, so there is no second copy of the install logic to drift. It execs rather than
    subprocesses so the running interpreter is replaced outright — a process that reinstalls the
    package it is importing from is asking for a half-updated venv.

    Deliberately does NOT check the CLI against the installed guest image: nothing declares which
    versions pair with which, so any such check would be comparing two numbers with no rule
    relating them.
    """
    import shutil
    import subprocess  # nosec B404
    import tempfile
    import urllib.request

    if os.geteuid() != 0:
        print(
            "chutes-cvm update needs root: it writes the venv and the /usr/local/bin shim.\n"
            "  sudo chutes-cvm update",
            file=sys.stderr,
        )
        return 1

    installer = _checkout_installer()
    if installer:
        repo = os.path.dirname(os.path.dirname(os.path.dirname(installer)))
        print(f"editable install from {repo} — updating the checkout first")
        if os.path.isdir(os.path.join(repo, ".git")):
            rc = subprocess.call(
                ["git", "-C", repo, "pull", "--ff-only"]
            )  # nosec B603 B607
            if rc != 0:
                print(
                    f"git pull failed in {repo}; resolve it there and re-run.",
                    file=sys.stderr,
                )
                return rc
        else:
            print(f"  {repo} is not a git checkout — reinstalling from it as-is.")
        os.execv("/bin/bash", ["bash", installer, "--editable"])  # nosec B606

    # Non-editable: the source was discarded at install time, so fetch the installer itself.
    url = (
        "https://raw.githubusercontent.com/chutesai/sek8s/"
        f"{args.ref}/src/chutes-cvm/install.sh"
    )
    print(f"non-editable install — fetching {url}")
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
        prefix="chutes-cvm-install.", suffix=".sh", delete=False
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # nosec B310
            shutil.copyfileobj(resp, tmp)
        tmp.close()
    except (
        Exception
    ) as exc:  # noqa: BLE001 — any fetch failure is the same user-facing problem
        tmp.close()
        os.unlink(tmp.name)
        print(
            f"could not fetch the installer for ref '{args.ref}': {exc}",
            file=sys.stderr,
        )
        return 1
    os.chmod(tmp.name, 0o755)  # nosec B103
    os.environ["SEK8S_REF"] = args.ref
    os.execv("/bin/bash", ["bash", tmp.name])  # nosec B606


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chutes-cvm",
        description="Operate and inspect Chutes confidential GPU VMs on this host.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"chutes-cvm {_installed_version()}",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    # Every operator command is a noun group whose args are forwarded verbatim to that
    # subpackage's own main (see _PASSTHROUGH / main): the entries below exist for
    # `chutes-cvm --help` visibility; main() intercepts them before argparse (which mishandles
    # leading options like --help/--image via REMAINDER), so no func is set.
    sub.add_parser(
        "guest",
        add_help=False,
        help="TDX VM lifecycle — launch / stop / down "
        "(args forwarded; `chutes-cvm guest --help`).",
    )
    sub.add_parser(
        "host",
        add_help=False,
        help="Host lifecycle + hardware — setup / verify / submit-profile / tune / restore / "
        "reset-gpus / vfio-wedged (args forwarded; `chutes-cvm host --help`).",
    )
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

    # Not a passthrough noun group: a self-contained utility so a miner can confirm which
    # chutes-cvm (and version) is actually on PATH. Also available as `chutes-cvm --version`.
    p_version = sub.add_parser(
        "version",
        help="Print the installed chutes-cvm version and where it resolves from.",
    )
    p_version.set_defaults(func=_cmd_version)

    # Same category as `version`: about this CLI, not about a managed noun. Re-runs install.sh,
    # which is the only installer — so `update` cannot drift from how the host was set up.
    p_update = sub.add_parser(
        "update",
        help="Update this CLI in place by re-running its installer (needs root).",
    )
    p_update.add_argument(
        "--ref",
        default="main",
        help="Branch or tag to install from (default: main, the released state — "
        "the same default install.sh uses).",
    )
    p_update.set_defaults(func=_cmd_update)

    return parser


# Commands whose arguments are forwarded verbatim to an underlying main(argv). Intercepted
# before argparse because REMAINDER mishandles leading options (e.g. `image --image`,
# `host --help`). Each underlying main owns its own --help. The low-level QEMU boot primitive
# (chutes_cvm.guest.vm) is not a CLI command — `guest launch` reaches it via import.
_PASSTHROUGH = (
    "guest",
    "host",
    "image",
    "config",
    "measurements",
)


def main(argv: "list[str] | None" = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in _PASSTHROUGH:
        forward = raw[1:]
        if raw[0] == "guest":
            from chutes_cvm.guest.cli import main as _guest_main

            return _guest_main(forward)
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
