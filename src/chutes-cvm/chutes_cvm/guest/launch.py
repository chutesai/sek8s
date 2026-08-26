"""End-to-end TDX VM launch orchestrator — ``chutes-cvm launch``.

This is the decision layer (ported from the former quick-launch.sh): parse args + config with
precedence (CLI > YAML > defaults), validate, run the host gates (TDX active, NUMA), refuse a
duplicate chutes-td, then perform each privileged step by invoking the bundled bash helper that
owns it (volumes, config volume, per-VM image, bridge), and finally boot via the launch-vm
primitive (``chutes_cvm.guest.__main__``). Per AGENT.md's bash-vs-Python rule, Python owns the
decisions and bash still owns the root system mutations (cryptsetup/mkfs/nbd, ip/iptables).

The privileged helpers create volumes with relative default names (``cache-<host>.raw`` …) and
reference sibling ``volumes/`` / ``network/`` scripts, so — exactly as quick-launch did — the
orchestration runs with the bundled scripts dir as its working directory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from chutes_cvm.guest.config import ConfigError, LaunchConfig
from chutes_cvm.paths import SCRIPTS_DIR, default_config_path

_PROCESS_NAME_CHUTES_TD = "chutes-td"


# ── Host gates ──────────────────────────────────────────────────────────────────


def _chutes_td_running() -> bool:
    """True if a live (non-zombie) chutes-td QEMU is already running.

    Kept aligned with ansible/host/roles/chutes_tee_vm/files/is_live_chutes_td.sh: match a
    qemu-system/qemu-kvm process whose cmdline carries the chutes-td process name.
    """
    try:
        pids = subprocess.run(
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


def _tdx_active() -> "tuple[bool, str]":
    """Return (active, source). Check sysfs first (survives dmesg rollover), then /proc/cpuinfo,
    then dmesg as a last resort — matching the former quick-launch Step 0."""
    try:
        with open("/sys/module/kvm_intel/parameters/tdx") as f:
            if f.read().strip() == "Y":
                return True, "sysfs (/sys/module/kvm_intel/parameters/tdx=Y)"
    except OSError:
        pass
    try:
        with open("/proc/cpuinfo") as f:
            if "tdx" in f.read():
                return True, "/proc/cpuinfo"
    except OSError:
        pass
    dmesg = subprocess.run(["sudo", "dmesg"], capture_output=True, text=True).stdout
    if any(
        "module initialized" in ln for ln in dmesg.splitlines() if "tdx" in ln.lower()
    ):
        return True, "dmesg"
    return False, ""


def _ensure_numa_zone_reclaim() -> None:
    """Ensure vm.zone_reclaim_mode=0 (cross-node allocation for QEMU/KVM); fix if not."""
    current = subprocess.run(
        ["sysctl", "-n", "vm.zone_reclaim_mode"], capture_output=True, text=True
    ).stdout.strip()
    if current != "0":
        print(f"⚠ vm.zone_reclaim_mode={current or 'unknown'} — setting to 0")
        subprocess.run(["sudo", "sysctl", "-w", "vm.zone_reclaim_mode=0"], check=False)
    print("✓ NUMA zone reclaim disabled (vm.zone_reclaim_mode=0)")


def _resolve_public_iface(configured: str) -> str:
    """Return the public interface: the configured one if it exists, else the default-route dev.

    Warns (but does not fail) when a configured name is missing — a stale NIC name after an OS
    upgrade is caught here rather than producing broken iptables rules.
    """
    if configured and _iface_exists(configured):
        return configured
    detected = _default_route_iface()
    if not detected:
        raise LaunchError(
            "could not determine the public interface — auto-detection found no default "
            "route. Set network.public_interface in config.yaml or pass --public-iface."
        )
    if configured:
        print(
            f"⚠ configured public interface '{configured}' not found; auto-detected "
            f"'{detected}' from the default route (update network.public_interface to silence)."
        )
    return detected


def _iface_exists(name: str) -> bool:
    return (
        subprocess.run(["ip", "link", "show", name], capture_output=True).returncode
        == 0
    )


def _default_route_iface() -> str:
    """The interface of the default route (empty if none)."""
    out = subprocess.run(
        ["ip", "-j", "route", "show", "default"], capture_output=True, text=True
    ).stdout.strip()
    try:
        routes = json.loads(out) if out else []
    except json.JSONDecodeError:
        return ""
    return routes[0].get("dev", "") if routes else ""


class LaunchError(Exception):
    """A launch precondition failed (message is user-facing)."""


# ── Privileged steps (bash helpers own the actual system mutations) ──────────────


def _helper(*parts: str) -> str:
    return str(SCRIPTS_DIR.joinpath(*parts))


def _volume_path(vol: str) -> str:
    """Resolve a (possibly relative) volume path the way the bash helper will — relative names
    live in the scripts working directory (SCRIPTS_DIR), matching the former quick-launch cwd.
    """
    return vol if os.path.isabs(vol) else str(SCRIPTS_DIR / vol)


def _ensure_raw_volume(vol: str, size: str, label: str, kind: str) -> None:
    """Create a raw LUKS volume via volumes/create-cache.sh unless it already exists.

    ``kind`` is only for messages. qcow2 volumes are never created (only reused if present).
    """
    path = _volume_path(vol)
    if os.path.exists(path):
        print(f"✓ Using existing {kind} volume: {vol}")
        return
    if vol.endswith(".qcow2"):
        raise LaunchError(
            f"qcow2 volumes cannot be created — use .raw for a new {kind} volume "
            f"(e.g. {kind}-<hostname>.raw). Existing qcow2 volumes are reused if present."
        )
    print(f"Creating {kind} volume at: {vol} ({size})")
    _run([_helper("volumes", "create-cache.sh"), vol, size, label])


def _setup_config_volume(cfg: dict, benchmark: bool) -> None:
    """Create/refresh the config volume via volumes/create-config.sh.

    Benchmark passes hostname + network positionally with empty miner creds; production passes
    every value by NAME through the environment (create-config.sh reads those), so long/optional
    fields (docker creds, operator key) stay off the command line.
    """
    vol = cfg["config_volume"]
    action = "Refreshing existing" if os.path.exists(_volume_path(vol)) else "Creating"
    print(f"{action} config volume: {vol}")
    gateway = cfg["bridge_ip"].split("/")[0]
    helper = _helper("volumes", "create-config.sh")
    if benchmark:
        _run(
            [
                "sudo",
                helper,
                vol,
                cfg["hostname"],
                "",
                "",
                cfg["vm_ip"],
                gateway,
                cfg["vm_dns"],
            ]
        )
    else:
        _run(
            [
                "sudo",
                f"HOSTNAME={cfg['hostname']}",
                f"MINER_SS58={cfg['miner_ss58']}",
                f"MINER_SEED={cfg['miner_seed']}",
                f"VM_IP={cfg['vm_ip']}",
                f"VM_GATEWAY={gateway}",
                f"VM_DNS={cfg['vm_dns']}",
                f"DOCKER_HUB_USER={cfg['docker_hub_username']}",
                f"DOCKER_HUB_TOKEN={cfg['docker_hub_token']}",
                f"OPERATOR_SIGNING_KEY={cfg['operator_signing_key']}",
                helper,
                vol,
            ]
        )


def _prepare_vm_image(base_image: str, hostname: str, vm_image_dir: str) -> str:
    """Verify the image set + instantiate the per-VM copy; return the per-VM image path."""
    proc = subprocess.run(
        [_helper("prepare-vm-image.sh"), base_image, hostname, vm_image_dir],
        cwd=str(SCRIPTS_DIR),
        capture_output=True,
        text=True,
    )
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        raise LaunchError("VM image preparation failed (see output above)")
    vm_image = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if not vm_image:
        raise LaunchError("prepare-vm-image did not return a VM image path")
    return vm_image


def _setup_bridge(cfg: dict) -> str:
    """Set up TAP bridge networking via network/setup-bridge.sh; return the TAP interface name."""
    proc = subprocess.run(
        [
            _helper("network", "setup-bridge.sh"),
            "--bridge-ip",
            cfg["bridge_ip"],
            "--vm-ip",
            f"{cfg['vm_ip']}/24",
            "--vm-dns",
            cfg["vm_dns"],
            "--public-iface",
            cfg["public_iface"],
            "--multi-queue",
        ],
        cwd=str(SCRIPTS_DIR),
        capture_output=True,
        text=True,
    )
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise LaunchError("bridge setup failed")
    for line in proc.stdout.splitlines():
        if line.startswith("Network interface:"):
            return line.split(":", 1)[1].strip()
    raise LaunchError("could not extract the TAP interface from setup-bridge output")


def _install_benchmark_netlog(cfg: dict) -> None:
    """Install + (re)start the benchmark network-logging service from the bundled network/ files."""
    net = SCRIPTS_DIR / "network"
    srcs = {
        "benchmark-netlog.sh": ("/usr/local/bin/benchmark-netlog.sh", "0755"),
        "benchmark-netlog.service": (
            "/etc/systemd/system/benchmark-netlog.service",
            "0644",
        ),
        "benchmark-netlog.logrotate": (
            "/etc/logrotate.d/benchmark-netlog",
            "0644",
        ),
    }
    for name, (dst, mode) in srcs.items():
        src = net / name
        if not src.exists():
            raise LaunchError(f"benchmark netlog source missing: {src}")
        _run(["sudo", "install", "-m", mode, str(src), dst])

    env_file = "/etc/chutes/benchmark-netlog.env"
    if not os.path.exists(env_file):
        _run(["sudo", "mkdir", "-p", "/etc/chutes"])
        content = f"BRIDGE_SUBNET={cfg['bridge_ip']}\nNETLOG_DIR=/var/log/chutes/benchmark-netlog\n"
        subprocess.run(
            ["sudo", "tee", env_file],
            input=content.encode(),
            stdout=subprocess.DEVNULL,
            check=True,
        )
    _run(["sudo", "systemctl", "daemon-reload"])
    subprocess.run(["sudo", "systemctl", "enable", "benchmark-netlog"], check=False)
    _run(["sudo", "systemctl", "restart", "benchmark-netlog"])
    print("✓ benchmark-netlog service installed and running")


def _run(cmd: "list[str]") -> None:
    """Run a privileged step from the scripts working directory; raise LaunchError on failure."""
    print(f"  $ {' '.join(cmd)}")
    if subprocess.run(cmd, cwd=str(SCRIPTS_DIR)).returncode != 0:
        raise LaunchError(f"command failed: {' '.join(cmd)}")


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
    "operator_signing_key": ("rc", "operator_signing_key"),
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
        prog="chutes-cvm launch",
        description="End-to-end TEE VM launch: verify host, prepare volumes and network, boot.",
        epilog=(
            "Related commands (formerly flags of this orchestrator): `chutes-cvm config init` "
            "(scaffold config.yaml), `chutes-cvm image download` (fetch a base set), "
            "`chutes-cvm down` / `stop` (tear down)."
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
    p.add_argument("--operator-signing-key", dest="operator_signing_key")
    p.add_argument("--skip-bind", action="store_true", default=None)
    p.add_argument("--no-gpus", action="store_true", default=None)
    p.add_argument("--foreground", action="store_true", default=None)
    p.add_argument("--ephemeral", action="store_true", default=None)
    p.add_argument("--benchmark", action="store_true", default=None)
    p.add_argument("--force", action="store_true", default=None)
    return p


def _resolve_config(args: argparse.Namespace) -> "tuple[dict, bool, bool, bool]":
    """Resolve config via the LaunchConfig model (CLI > env > YAML > defaults) and return
    (flat_cfg, benchmark, pass_gpus, ephemeral). The last three are launch-runtime flags, not
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

    return model.flat(), bool(args.benchmark), not args.no_gpus, bool(args.ephemeral)


def _apply_derived_defaults(cfg: dict, benchmark: bool, ephemeral: bool) -> None:
    """Fill benchmark placeholders, the default base image, VM-image dir, and volume names."""
    if benchmark:
        cfg["base_image"] = (
            cfg["base_image"] or "/var/lib/chutes/base-images/tdx-guest-benchmark"
        )
        cfg["miner_ss58"] = cfg["miner_ss58"] or "benchmark"
        cfg["miner_seed"] = cfg["miner_seed"] or "benchmark"

    cfg["base_image"] = cfg["base_image"] or "/var/lib/chutes/base-images/tdx-guest"
    if ephemeral:
        cfg["vm_image_dir"] = "/tmp/chutes-vm-images"
    else:
        cfg["vm_image_dir"] = cfg["vm_image_dir"] or "/var/lib/chutes/vm-images"

    cfg["cache_volume"] = cfg["cache_volume"] or f"cache-{cfg['hostname']}.raw"
    cfg["storage_volume"] = cfg["storage_volume"] or f"storage-{cfg['hostname']}.raw"
    cfg["config_volume"] = cfg["config_volume"] or f"config-{cfg['hostname']}.qcow2"


def _validate(cfg: dict, benchmark: bool) -> None:
    if cfg["network_type"] not in ("tap", "user"):
        raise LaunchError("network type must be 'tap' or 'user'")
    missing = []
    if not cfg["hostname"]:
        missing.append("hostname (vm.hostname or --hostname)")
    if not benchmark:
        if not cfg["miner_ss58"]:
            missing.append("miner.ss58 (miner.ss58 or --miner-ss58)")
        if not cfg["miner_seed"]:
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
        cfg, benchmark, pass_gpus, ephemeral = _resolve_config(args)
        cfg["public_iface"] = _resolve_public_iface(cfg["public_iface"])
        _apply_derived_defaults(cfg, benchmark, ephemeral)
        _validate(cfg, benchmark)
    except LaunchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("\n=== TEE VM Orchestration ===")
    print(f"Mode: {'benchmark' if benchmark else 'standard'}")
    print(f"Hostname: {cfg['hostname']}")
    print(f"Base image: {cfg['base_image']}")
    print(f"VM image dir: {cfg['vm_image_dir']}")
    print(f"Network: {cfg['network_type']}\n")

    if not args.force and _chutes_td_running():
        print(
            f"Error: a TDX VM (QEMU, {_PROCESS_NAME_CHUTES_TD}) is already running.\n"
            "  Stop it first: chutes-cvm down  (or pass --force to override — not recommended).",
            file=sys.stderr,
        )
        return 1

    print("Step 0: Verifying host configuration...")
    active, source = _tdx_active()
    if not active:
        print(
            "✗ TDX does not appear active (checked sysfs, /proc/cpuinfo, dmesg). Enable TDX in "
            "BIOS + kernel and reboot; verify with `cat /sys/module/kvm_intel/parameters/tdx`.",
            file=sys.stderr,
        )
        return 1
    print(f"✓ TDX active (via {source})")
    _ensure_numa_zone_reclaim()

    orig_cwd = os.getcwd()
    os.chdir(
        str(SCRIPTS_DIR)
    )  # helpers create relative volumes / call sibling scripts here
    try:
        if not benchmark:
            print("\nStep 2: Preparing cache volume...")
            _ensure_raw_volume(
                cfg["cache_volume"], cfg["cache_size"], "tdx-cache", "cache"
            )

        print("\nStep 3: Preparing storage volume...")
        _ensure_raw_volume(
            cfg["storage_volume"], cfg["storage_size"], "storage", "storage"
        )

        print("\nStep 4: Setting up config volume...")
        _setup_config_volume(cfg, benchmark)

        print("\nStep 4b: Preparing VM image (verify set + per-VM copy)...")
        vm_image = _prepare_vm_image(
            cfg["base_image"], cfg["hostname"], cfg["vm_image_dir"]
        )

        net_iface = ""
        if cfg["network_type"] == "tap":
            print("\nStep 5: Setting up bridge networking...")
            net_iface = _setup_bridge(cfg)
            print(f"✓ Bridge configured (TAP: {net_iface})")
            if benchmark:
                print("\nStep 5b: Installing benchmark network logging...")
                _install_benchmark_netlog(cfg)

        rc = _boot(cfg, vm_image, net_iface, benchmark, pass_gpus)
    except LaunchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        os.chdir(orig_cwd)

    if rc != 0:
        print(
            "\nError: VM launch failed (launch-vm exited non-zero). See output above and "
            "/tmp/tdx-guest-td.log if daemonized.",
            file=sys.stderr,
        )
        return rc
    print("\n=== Chutes VM Deployed Successfully ===\n")
    return 0


def _boot(
    cfg: dict, vm_image: str, net_iface: str, benchmark: bool, pass_gpus: bool
) -> int:
    """Assemble the launch-vm argument list and call the QEMU primitive in-process."""
    # Deferred import: the launch-vm primitive pulls the heavy, host-specific chain
    # (detection/gpu/qemu/passthrough) that only an actual boot needs — importing it here keeps
    # `launch --help` and the early config/gate paths light.
    from chutes_cvm.guest.__main__ import main as launch_vm_main

    launch_args = ["--image", vm_image, "--network-type", cfg["network_type"]]
    if pass_gpus:
        launch_args.append("--pass-gpus")
    if cfg["network_type"] == "tap":
        launch_args += ["--net-iface", net_iface]
    if benchmark:
        # Benchmark: no cache volume (partner manages storage); config volume carries only
        # hostname + network; --ssh shows the login hint.
        launch_args += ["--ssh", "--config-volume", cfg["config_volume"]]
        launch_args += ["--storage-volume", cfg["storage_volume"]]
    else:
        launch_args += ["--config-volume", cfg["config_volume"]]
        launch_args += ["--cache-volume", cfg["cache_volume"]]
        launch_args += ["--storage-volume", cfg["storage_volume"]]
    if cfg["foreground"]:
        launch_args.append("--foreground")

    print("\nLaunching Chutes VM...")
    return launch_vm_main(launch_args)


if __name__ == "__main__":
    sys.exit(main())
