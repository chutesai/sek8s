"""End-to-end TDX VM launch orchestrator — ``chutes-cvm guest launch``.

This is the decision layer (ported from the former quick-launch.sh): parse args + config with
precedence (CLI > YAML > defaults), validate, run the host gates (TDX active, NUMA), refuse a
duplicate chutes-td, then perform each privileged step — invoking the bundled bash helper that
owns it for the ones whose logic *is* a sequence of special-tool calls (volumes via
cryptsetup/nbd, config volume, bridge via ip/iptables), or doing it in-process where it is plain
file work (the per-VM image copy + sidecar staging, as `sudo cp`/`mkdir`/`rm`) — and finally boot
via the QEMU boot primitive (``chutes_cvm.guest.vm``). Per AGENT.md's bash-vs-Python rule,
Python owns the decisions and bash still owns the tool-sequence system mutations.

The privileged helpers create volumes with relative default names (``cache-<host>.raw`` …) and
reference sibling ``volumes/`` / ``network/`` scripts, so — exactly as quick-launch did — the
orchestration runs with the bundled scripts dir as its working directory.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Literal, get_args, get_origin

from chutes_cvm import proc
from chutes_cvm.guest.chutes_api import DEFAULT_API_BASE, ChutesApiError
from chutes_cvm.guest.config import ConfigError, LaunchConfig, cli_fields
from chutes_cvm.guest.context import (
    GuestNetwork,
    GuestVolumes,
    LaunchContext,
    ProcessBundle,
)
from chutes_cvm.guest.host_class import HostClass, MeasuredImage, NotMeasured
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.image_set import ImageSet
from chutes_cvm.guest.images import prepare_vm_image
from chutes_cvm.guest.network import (
    install_benchmark_netlog,
    resolve_public_iface,
    setup_bridge,
)
from chutes_cvm.guest.privileged import LaunchError
from chutes_cvm.guest.vm import (
    LOGFILE,
    PIDFILE,
    PROCESS_NAME,
    device_blockers,
    launch_vm,
)
from chutes_cvm.guest.volumes import ensure_raw_volume, setup_config_volume
from chutes_cvm.paths import SCRIPTS_DIR, default_config_path

# ── Host gates ──────────────────────────────────────────────────────────────────


def _ensure_numa_zone_reclaim() -> None:
    """Ensure vm.zone_reclaim_mode=0 (cross-node allocation for QEMU/KVM); fix if not."""
    current = proc.run(
        ["sysctl", "-n", "vm.zone_reclaim_mode"], capture_output=True, text=True
    ).stdout.strip()
    if current != "0":
        print(f"⚠ vm.zone_reclaim_mode={current or 'unknown'} — setting to 0")
        proc.run(["sudo", "sysctl", "-w", "vm.zone_reclaim_mode=0"], check=False)
    print("✓ NUMA zone reclaim disabled (vm.zone_reclaim_mode=0)")


def _measured_image(
    config_path: str, image_set_dir: str, force: bool, host_profile: HostProfile
) -> "MeasuredImage | None":
    """Step 1: is this image measured for this host's class? Returns its entry, or None for a
    test boot.

    Fetches the class (as `chutes-cvm host verify` does) for ``host_profile`` -- the reading Step
    0 took and the boot builds from -- and looks up the image's ``(version, rc)``. Measured, it
    launches. Otherwise a debug build or ``--force`` gets a test boot, which boots but cannot
    attest; a production image is refused here, before any volume or GPU work, because it would
    boot and then fail attestation. An unreadable manifest or no answer from the API counts as
    not measured.
    """
    try:
        image_set = ImageSet.from_dir(image_set_dir)
    except (FileNotFoundError, ValueError, OSError) as exc:
        # Can't read the manifest -> can't know what we're booting, or whether it is a debug build.
        _test_boot_or_refuse(
            f"could not read image version from {image_set_dir}: {exc}",
            "Re-run `chutes-cvm image download`.",
            debug=False,
            force=force,
        )
        return None

    api_base = os.environ.get("CHUTES_API_BASE") or DEFAULT_API_BASE
    try:
        host_class = HostClass.fetch(
            host_profile, config_path=config_path, api_base=api_base
        )
    except ChutesApiError as exc:
        _test_boot_or_refuse(
            f"could not confirm this host can attest {image_set.label}: {exc}",
            "Retry once the API is reachable.",
            debug=image_set.rc,
            force=force,
        )
        return None
    try:
        measured = host_class.measured_image(image_set)
    except NotMeasured:
        detail = f" {host_class.detail}" if host_class.detail else ""
        _test_boot_or_refuse(
            f"this host cannot attest {image_set.label} yet "
            f"(fingerprint {host_class.fingerprint}).{detail}",
            "Register this host class with `chutes-cvm host submit-profile`, then retry once "
            "Chutes\n  publishes the measurement (`chutes-cvm host verify` shows readiness).",
            debug=image_set.rc,
            force=force,
        )
        return None
    print(
        f"✓ {image_set.label} is measured for this host class "
        f"(fingerprint {host_class.fingerprint})"
    )
    return measured


def _test_boot_or_refuse(
    problem: str, remedy: str, *, debug: bool, force: bool
) -> None:
    """Allow a test boot for a debug build or ``--force``, with a warning; otherwise raise
    ``LaunchError``."""
    if debug or force:
        reason = "debug build" if debug else "--force"
        print(
            f"⚠ {problem}\n  Test boot ({reason}): the VM boots but will not attest.",
            file=sys.stderr,
        )
        return None
    raise LaunchError(
        f"{problem}\n  Refusing to launch: the VM would boot but fail attestation.\n"
        f"  {remedy} Pass --force to launch anyway."
    )


# ── Privileged steps (bash helpers own the actual system mutations) ──────────────


# ── Argument parsing + config precedence ─────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chutes-cvm guest launch",
        # No prefix matching. With it on, a removed or mistyped flag silently resolves to
        # whatever it is a prefix of -- `--config` became `--config-volume`, so a launch config
        # path was read as a config VOLUME path. It also means adding a flag can break existing
        # automation by making an abbreviation it relied on ambiguous.
        allow_abbrev=False,
        description="End-to-end TEE VM launch: verify host, prepare volumes and network, boot.",
        epilog=(
            "Related commands (formerly flags of this orchestrator): `chutes-cvm config init` "
            "(scaffold config.yaml), `chutes-cvm image download` (fetch a base set), "
            "`chutes-cvm guest down` / `stop` (tear down)."
        ),
    )
    # Positional only. There was a `--config` sharing this dest, and it never worked: an
    # optional positional applies its default even when it matches zero arguments, so it
    # clobbered whatever the flag had set and the launch silently fell back to the default
    # config path. An inert flag that looks like it is doing something is worse than no flag.
    p.add_argument(
        "config_file", nargs="?", help="Launch config.yaml (CLI flags override it)"
    )
    # Every flag that maps to a config setting comes from the model, which owns its YAML key,
    # env var, default, description and flag together. Declaring them here as well is what let
    # `network.ssh_port` exist on both sides and be plumbed on neither.
    for flag, path, annotation, help_ in cli_fields():
        # No explicit dest: argparse derives `--vm-dns` -> `vm_dns`, which is exactly what the
        # hand-written flags set, so every existing `args.<name>` reference keeps working.
        kwargs: dict = {"default": None, "help": help_}
        if annotation is bool:
            # store_true with default=None: absent stays None, so it cannot overwrite a YAML
            # true with False the way argparse's own default would.
            kwargs["action"] = "store_true"
        elif get_origin(annotation) is Literal:
            kwargs["choices"] = list(get_args(annotation))
        elif annotation is int:
            kwargs["type"] = int
        p.add_argument(flag, **kwargs)

    # Not derived from the model. --benchmark/--ephemeral/--no-gpus/--force pick the shape of
    # the launch and are returned alongside the config rather than folded into it; --config-file
    # says where to read it from; --skip-bind is the one config flag that names the NEGATION of
    # its field (devices.bind_devices), so it cannot be "that field's flag".
    p.add_argument(
        "--skip-bind",
        action="store_true",
        default=None,
        help="Do not bind GPU/NVSwitch to vfio-pci (devices.bind_devices: false)",
    )
    p.add_argument(
        "--no-gpus",
        action="store_true",
        default=None,
        help="Boot without passing any GPUs through (debug)",
    )
    p.add_argument(
        "--ephemeral",
        action="store_true",
        default=None,
        help="Put the per-VM image under /tmp/chutes-vm-images instead of the config's dir",
    )
    p.add_argument(
        "--benchmark",
        action="store_true",
        default=None,
        help="Launch the benchmark image; miner credentials default to placeholders",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=None,
        help="Launch despite a failed pre-launch check (the VM may fail attestation)",
    )
    return p


def _resolve_config(
    args: argparse.Namespace,
) -> "tuple[LaunchConfig, bool, bool, bool]":
    """Resolve config via the LaunchConfig model (CLI > env > YAML > defaults) and return
    (config, benchmark, pass_gpus, ephemeral). The last three are launch-runtime flags, not
    persisted config, so they stay out of the model."""
    # Docker Hub creds must be set together when given on the CLI.
    if bool(args.docker_hub_username) != bool(args.docker_hub_token):
        raise LaunchError(
            "use both --docker-hub-username and --docker-hub-token together (or neither)."
        )

    # Build nested CLI overrides (only flags the user set) — the highest-precedence source.
    overrides: dict = {}
    for flag, path, _annotation, _help in cli_fields():
        val = getattr(args, flag.lstrip("-").replace("-", "_"), None)
        if val is None:
            continue
        node = overrides
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = val
    if args.skip_bind:
        overrides.setdefault("devices", {})["bind_devices"] = False

    if args.config_file:
        print(f"Loading configuration from: {args.config_file}")
    try:
        model = LaunchConfig.from_file(args.config_file, **overrides)
    except ConfigError as exc:
        raise LaunchError(f"config: {exc}") from exc
    if args.config_file:
        print("✓ Configuration loaded")

    return model, bool(args.benchmark), not args.no_gpus, bool(args.ephemeral)


def _apply_derived_defaults(
    config: LaunchConfig, benchmark: bool, ephemeral: bool
) -> None:
    """Fill benchmark placeholders, the default base image, VM-image dir, and volume names."""
    if benchmark:
        config.vm.base_image = (
            config.vm.base_image or "/var/lib/chutes/base-images/tdx-guest-benchmark"
        )
        config.miner.ss58 = config.miner.ss58 or "benchmark"
        config.miner.seed = config.miner.seed or "benchmark"

    config.vm.base_image = (
        config.vm.base_image or "/var/lib/chutes/base-images/tdx-guest"
    )
    if ephemeral:
        config.vm.vm_image_directory = "/tmp/chutes-vm-images"  # nosec B108
    else:
        config.vm.vm_image_directory = (
            config.vm.vm_image_directory or "/var/lib/chutes/vm-images"
        )

    config.volumes.cache.path = (
        config.volumes.cache.path or f"cache-{config.vm.hostname}.raw"
    )
    config.volumes.storage.path = (
        config.volumes.storage.path or f"storage-{config.vm.hostname}.raw"
    )
    config.volumes.config.path = (
        config.volumes.config.path or f"config-{config.vm.hostname}.qcow2"
    )


def _validate(config: LaunchConfig, benchmark: bool) -> None:
    if config.network.type not in ("tap", "user"):
        raise LaunchError("network type must be 'tap' or 'user'")
    missing = []
    if not config.vm.hostname:
        missing.append("hostname (vm.hostname or --hostname)")
    if not benchmark:
        if not config.miner.ss58:
            missing.append("miner.ss58 (miner.ss58 or --miner-ss58)")
        if not (config.miner.private_key or config.miner.seed):
            missing.append(
                "miner.private_key or miner.seed (--miner-private-key or --miner-seed)"
            )
    if missing:
        raise LaunchError(
            "missing required configuration:\n  - " + "\n  - ".join(missing)
        )


# ── Orchestration ────────────────────────────────────────────────────────────────


def main(argv: "list[str] | None" = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.config_file is None:
        default_cfg = default_config_path()
        if default_cfg and os.path.exists(default_cfg):
            args.config_file = default_cfg
    # Resolve the config path against the caller's cwd before we switch to the scripts dir.
    if args.config_file:
        args.config_file = os.path.abspath(args.config_file)

    try:
        config, benchmark, pass_gpus, ephemeral = _resolve_config(args)
        config.network.public_interface = resolve_public_iface(
            config.network.public_interface
        )
        _apply_derived_defaults(config, benchmark, ephemeral)
        _validate(config, benchmark)
    except LaunchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("\n=== TEE VM Orchestration ===")
    print(f"Mode: {'benchmark' if benchmark else 'standard'}")
    print(f"Hostname: {config.vm.hostname}")
    print(f"Base image: {config.vm.base_image}")
    print(f"VM image dir: {config.vm.vm_image_directory}")
    print(f"Network: {config.network.type}\n")

    # One predicate, and --force only reaches the blockers that say it may. A VM that is
    # merely running is the operator's call to override; a reclaim is not, because forcing past
    # it reaches the unbind, and the unbind is what costs the host its ability to reboot.
    blockers = [b for b in device_blockers() if not (args.force and b.overridable)]
    if blockers:
        for blocker in blockers:
            print(f"Error: {blocker.detail}", file=sys.stderr)
        return 1

    print("Step 0: Verifying host configuration...")
    # THE reading of this host for this launch. discover-profile.sh is the single reader --
    # preflight signs this profile and the boot primitive builds the command from it, so both
    # see the same hardware. A second read could disagree with the one the API approved.
    host = HostProfile.from_host()
    try:
        host.verify_environment()
    except RuntimeError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(f"✓ {host.tee_provider.label} active")
    _ensure_numa_zone_reclaim()

    # Benchmark VMs use dummy creds and aren't attested, so they test boot without asking. A debug
    # (RC) image is gated like any other: a published rc:true measurement launches it measured.
    measured: "MeasuredImage | None" = None
    if not benchmark:
        print("\nStep 1: Confirming this host can attest the image...")
        try:
            measured = _measured_image(
                args.config_file or default_config_path(),
                config.vm.base_image,
                bool(args.force),
                host,
            )
        except LaunchError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 1

    orig_cwd = os.getcwd()
    os.chdir(
        str(SCRIPTS_DIR)
    )  # helpers create relative volumes / call sibling scripts here
    try:
        if not benchmark:
            print("\nStep 2: Preparing cache volume...")
            ensure_raw_volume(
                config.volumes.cache.path,
                config.volumes.cache.size,
                "tdx-cache",
                "cache",
            )

        print("\nStep 3: Preparing storage volume...")
        ensure_raw_volume(
            config.volumes.storage.path,
            config.volumes.storage.size,
            "storage",
            "storage",
        )

        print("\nStep 4: Setting up config volume...")
        setup_config_volume(config, benchmark)

        print("\nStep 4b: Preparing VM image (verify set + per-VM copy)...")
        vm_image = prepare_vm_image(
            config.vm.base_image, config.vm.hostname, config.vm.vm_image_directory
        )

        net_iface = ""
        if config.network.type == "tap":
            print("\nStep 5: Setting up bridge networking...")
            net_iface = setup_bridge(config)
            print(f"✓ Bridge configured (TAP: {net_iface})")
            if benchmark:
                print("\nStep 5b: Installing benchmark network logging...")
                install_benchmark_netlog(config)

        rc = _boot(config, vm_image, net_iface, benchmark, pass_gpus, host, measured)
    except LaunchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        os.chdir(orig_cwd)

    if rc != 0:
        print(
            "\nError: VM launch failed (the QEMU boot exited non-zero). See output above and "
            "/tmp/tdx-guest-td.log if daemonized.",
            file=sys.stderr,
        )
        return rc
    print("\n=== Chutes VM Deployed Successfully ===\n")
    return 0


def _boot(
    config: LaunchConfig,
    vm_image: str,
    net_iface: str,
    benchmark: bool,
    pass_gpus: bool,
    host: HostProfile,
    measured: "MeasuredImage | None",
) -> int:
    """Build this guest's context and call the QEMU primitive in-process. ``measured`` is Step
    1's entry for this image; None is a test boot."""
    volumes = GuestVolumes(
        config=config.volumes.config.path,
        # Benchmark guests have no cache volume: the partner manages storage.
        cache=None if benchmark else config.volumes.cache.path,
        storage=config.volumes.storage.path,
    )
    network = GuestNetwork(
        network_type=config.network.type,
        # Only tap mode has one; user mode forwards a port instead.
        net_iface=net_iface or None,
        ssh_port=config.network.ssh_port,
    )
    process = ProcessBundle(PROCESS_NAME, config.runtime.foreground, PIDFILE, LOGFILE)
    guest = LaunchContext.from_host(
        host,
        measured,
        image=vm_image,
        volumes=volumes,
        network=network,
        process=process,
        pass_gpus=pass_gpus,
        show_ssh=benchmark,  # benchmark guests print the login hint
    )

    print("\nLaunching Chutes VM...")
    return launch_vm(guest, host)


if __name__ == "__main__":
    sys.exit(main())
