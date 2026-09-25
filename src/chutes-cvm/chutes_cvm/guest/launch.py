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

from chutes_cvm import proc
from chutes_cvm.guest import image_set
from chutes_cvm.guest.config import ConfigError, LaunchConfig
from chutes_cvm.guest.context import GuestContext
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.images import prepare_vm_image
from chutes_cvm.guest.network import (
    install_benchmark_netlog,
    resolve_public_iface,
    setup_bridge,
)
from chutes_cvm.guest.preflight import DEFAULT_API_BASE, PreflightError, run_preflight
from chutes_cvm.guest.privileged import LaunchError
from chutes_cvm.guest.qemu import GuestNetwork, GuestVolumes
from chutes_cvm.guest.vm import launch_vm
from chutes_cvm.guest.volumes import ensure_raw_volume, setup_config_volume
from chutes_cvm.paths import SCRIPTS_DIR, default_config_path

_PROCESS_NAME_CHUTES_TD = "chutes-td"


# ── Host gates ──────────────────────────────────────────────────────────────────


def _chutes_td_running() -> bool:
    """True if a live (non-zombie) chutes-td QEMU is already running.

    Kept aligned with ansible/host/roles/chutes_tee_vm/files/is_live_chutes_td.sh: match a
    qemu-system/qemu-kvm process whose cmdline carries the chutes-td process name.
    """
    try:
        pids = proc.run(
            ["pgrep", "-f", "qemu-system|qemu-kvm"],
            capture_output=True,
            text=True,
        ).stdout.split()
    except FileNotFoundError:
        return False
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\x00", b" ").decode(errors="replace")
        except OSError:
            continue
        if "qemu-system" not in cmdline and "qemu-kvm" not in cmdline:
            continue
        if _PROCESS_NAME_CHUTES_TD in cmdline:
            return True
    return False


def _ensure_numa_zone_reclaim() -> None:
    """Ensure vm.zone_reclaim_mode=0 (cross-node allocation for QEMU/KVM); fix if not."""
    current = proc.run(
        ["sysctl", "-n", "vm.zone_reclaim_mode"], capture_output=True, text=True
    ).stdout.strip()
    if current != "0":
        print(f"⚠ vm.zone_reclaim_mode={current or 'unknown'} — setting to 0")
        proc.run(["sudo", "sysctl", "-w", "vm.zone_reclaim_mode=0"], check=False)
    print("✓ NUMA zone reclaim disabled (vm.zone_reclaim_mode=0)")


def _launchable(
    config_path: str, base_image: str, force: bool, host_profile: "HostProfile"
) -> bool:
    """Return True if launch may proceed: the control plane confirms an image of THIS host's
    ``(version, rc)`` will attest here. Mirrors `chutes-cvm host verify`'s API check — read the
    image's (version, rc) from its manifest, capture + sign the host profile, and ask
    POST /servers/tdx/preflight. Without a launchable verdict the VM would boot and then fail
    attestation, so refuse early (return False) unless ``force`` overrides with a warning.

    ``host_profile`` is the reading Step 0 took and the boot primitive will build from, so the
    verdict is about the shape that actually launches rather than a second, independent read.
    """
    try:
        version, rc = image_set.version_and_rc(base_image)
    except (FileNotFoundError, ValueError, OSError) as exc:
        # Can't read the manifest -> can't know what we're booting. Fail closed unless forced.
        if force:
            print(
                f"⚠ could not read image version from {base_image} ({exc}); proceeding anyway "
                "(--force) — attestation may fail.",
                file=sys.stderr,
            )
            return True
        print(
            f"✗ could not read image version from {base_image}: {exc}\n"
            "  Refusing to launch. Re-run `chutes-cvm image download`, or pass --force.",
            file=sys.stderr,
        )
        return False

    label = f"{version}{' (rc)' if rc else ''}"
    api_base = os.environ.get("CHUTES_API_BASE") or DEFAULT_API_BASE
    try:
        resp = run_preflight(
            config_path=config_path,
            version=version,
            rc=rc,
            api_base=api_base,
            host_profile=host_profile,
        )
        launchable = bool(resp.get("launchable"))
        fingerprint = resp.get("fingerprint", "?")
        detail = resp.get("detail", "")
    except PreflightError as exc:
        launchable, fingerprint, detail = False, "?", str(exc)

    if launchable:
        print(f"✓ {detail} (fingerprint {fingerprint})")
        return True

    problem = f"this host cannot attest {label} yet (fingerprint {fingerprint})." + (
        f" {detail}" if detail else ""
    )
    remedy = (
        "Register this host class with `chutes-cvm host submit-profile`, then retry once Chutes\n"
        "  publishes the measurement (`chutes-cvm host verify` shows readiness)."
    )
    if force:
        print(
            f"⚠ {problem}\n  Proceeding anyway (--force) — the VM will fail attestation if this "
            "image is truly unmeasured for this host.",
            file=sys.stderr,
        )
        return True
    print(
        f"✗ {problem}\n"
        "  Refusing to launch: the VM would boot but fail attestation.\n"
        f"  {remedy} Pass --force to launch anyway.",
        file=sys.stderr,
    )
    return False


# ── Privileged steps (bash helpers own the actual system mutations) ──────────────


# ── Argument parsing + config precedence ─────────────────────────────────────────

# CLI value flag (argparse dest) → its (section, key) in the nested LaunchConfig. Deeper volume
# fields are handled separately below. store_true flags are handled separately too.
_CLI_TO_SECTION = {
    "hostname": ("vm", "hostname"),
    "base_image": ("vm", "base_image"),
    "vm_image_dir": ("vm", "vm_image_directory"),
    "miner_ss58": ("miner", "ss58"),
    "miner_seed": ("miner", "seed"),
    "vm_ip": ("network", "vm_ip"),
    "bridge_ip": ("network", "bridge_ip"),
    "vm_dns": ("network", "dns"),
    "public_iface": ("network", "public_interface"),
    "network_type": ("network", "type"),
    "ssh_port": ("network", "ssh_port"),
    "docker_hub_username": ("docker_hub", "username"),
    "docker_hub_token": ("docker_hub", "token"),
}

# CLI volume flags → (volumes subsection, key).
_CLI_TO_VOLUME = {
    "cache_size": ("cache", "size"),
    "cache_volume": ("cache", "path"),
    "storage_size": ("storage", "size"),
    "storage_volume": ("storage", "path"),
    "config_volume": ("config", "path"),
}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chutes-cvm guest launch",
        description="End-to-end TEE VM launch: verify host, prepare volumes and network, boot.",
        epilog=(
            "Related commands (formerly flags of this orchestrator): `chutes-cvm config init` "
            "(scaffold config.yaml), `chutes-cvm image download` (fetch a base set), "
            "`chutes-cvm guest down` / `stop` (tear down)."
        ),
    )
    p.add_argument(
        "config_file", nargs="?", help="Launch config.yaml (CLI flags override it)"
    )
    p.add_argument("--config", dest="config_file", help="config.yaml path (explicit)")
    p.add_argument("--hostname")
    p.add_argument("--base-image", dest="base_image")
    p.add_argument("--vm-image-dir", dest="vm_image_dir")
    p.add_argument("--miner-ss58", dest="miner_ss58")
    p.add_argument("--miner-seed", dest="miner_seed")
    p.add_argument("--vm-ip", dest="vm_ip")
    p.add_argument("--bridge-ip", dest="bridge_ip")
    p.add_argument("--vm-dns", dest="vm_dns")
    p.add_argument("--public-iface", dest="public_iface")
    p.add_argument("--cache-size", dest="cache_size")
    p.add_argument("--cache-volume", dest="cache_volume")
    p.add_argument("--storage-size", dest="storage_size")
    p.add_argument("--storage-volume", dest="storage_volume")
    p.add_argument("--config-volume", dest="config_volume")
    p.add_argument("--ssh-port", dest="ssh_port", type=int)
    p.add_argument("--network-type", dest="network_type", choices=["tap", "user"])
    p.add_argument("--docker-hub-username", dest="docker_hub_username")
    p.add_argument("--docker-hub-token", dest="docker_hub_token")
    p.add_argument("--skip-bind", action="store_true", default=None)
    p.add_argument("--no-gpus", action="store_true", default=None)
    p.add_argument("--foreground", action="store_true", default=None)
    p.add_argument("--ephemeral", action="store_true", default=None)
    p.add_argument("--benchmark", action="store_true", default=None)
    p.add_argument("--force", action="store_true", default=None)
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
    for dest, (section, key) in _CLI_TO_SECTION.items():
        val = getattr(args, dest)
        if val is not None:
            overrides.setdefault(section, {})[key] = val
    for dest, (sub, key) in _CLI_TO_VOLUME.items():
        val = getattr(args, dest)
        if val is not None:
            overrides.setdefault("volumes", {}).setdefault(sub, {})[key] = val
    if args.foreground:
        overrides.setdefault("runtime", {})["foreground"] = True
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
        if not config.miner.seed:
            missing.append("miner.seed (miner.seed or --miner-seed)")
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

    if not args.force and _chutes_td_running():
        print(
            f"Error: a confidential VM (QEMU, {_PROCESS_NAME_CHUTES_TD}) is already running.\n"
            "  Stop it first: chutes-cvm guest down  (or pass --force to override — not recommended).",
            file=sys.stderr,
        )
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

    # The gate is a launch-readiness check: does a published measurement for THIS image's
    # (version, rc) cover this host class? Benchmark VMs use dummy creds and aren't attested, so
    # they skip it; a debug (RC) image is not special-cased — its rc:true measurement must be
    # published just like a production image's, which the (version, rc) join checks directly.
    if benchmark:
        pass
    else:
        print("\nStep 1: Confirming this host can attest the image...")
        if not _launchable(
            args.config_file or default_config_path(),
            config.vm.base_image,
            args.force,
            host,
        ):
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

        rc = _boot(config, vm_image, net_iface, benchmark, pass_gpus, host)
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
) -> int:
    """Build this guest's context and call the QEMU primitive in-process."""
    guest = GuestContext(
        image=vm_image,
        volumes=GuestVolumes(
            config=config.volumes.config.path,
            # Benchmark guests have no cache volume: the partner manages storage.
            cache=None if benchmark else config.volumes.cache.path,
            storage=config.volumes.storage.path,
        ),
        network=GuestNetwork(
            network_type=config.network.type,
            # Only tap mode has one; user mode forwards a port instead.
            net_iface=net_iface or None,
            ssh_port=config.network.ssh_port,
        ),
        pass_gpus=pass_gpus,
        foreground=config.runtime.foreground,
        # Benchmark guests print the login hint.
        show_ssh=benchmark,
    )

    print("\nLaunching Chutes VM...")
    return launch_vm(guest, host)


if __name__ == "__main__":
    sys.exit(main())
