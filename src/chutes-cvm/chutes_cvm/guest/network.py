"""Materializing a guest's network: resolve the host interface, then build its bridge.

Stage 3 of a launch. Tap mode needs a real host interface and a bridge with a TAP device on it;
user mode forwards a port instead and needs none of this.

The NIC the command attaches is unconditional either way -- it occupies a measured pcie.0 slot
whether or not anything backs it. Only the ``-netdev`` behind it depends on what happens here,
and a netdev is not a PCI device and is not measured.
"""

import json
import os
import sys

from chutes_cvm import proc
from chutes_cvm.guest.config import LaunchConfig
from chutes_cvm.guest.privileged import LaunchError, run
from chutes_cvm.paths import SCRIPTS_DIR


def resolve_public_iface(configured: str) -> str:
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
    return proc.run(["ip", "link", "show", name], capture_output=True).returncode == 0


def _default_route_iface() -> str:
    """The interface of the default route (empty if none)."""
    out = proc.run(
        ["ip", "-j", "route", "show", "default"], capture_output=True, text=True
    ).stdout.strip()
    try:
        routes = json.loads(out) if out else []
    except json.JSONDecodeError:
        return ""
    return routes[0].get("dev", "") if routes else ""


def setup_bridge(config: LaunchConfig) -> str:
    """Set up TAP bridge networking via network/setup-bridge.sh; return the TAP interface name."""
    result = proc.run(
        [
            str(SCRIPTS_DIR / "network" / "setup-bridge.sh"),
            "--bridge-ip",
            config.network.bridge_ip,
            "--vm-ip",
            f"{config.network.vm_ip}/24",
            "--vm-dns",
            config.network.dns,
            "--public-iface",
            config.network.public_interface,
            "--multi-queue",
        ],
        cwd=str(SCRIPTS_DIR),
        capture_output=True,
        text=True,
    )
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise LaunchError("bridge setup failed")
    for line in result.stdout.splitlines():
        if line.startswith("Network interface:"):
            return line.split(":", 1)[1].strip()
    raise LaunchError("could not extract the TAP interface from setup-bridge output")


def install_benchmark_netlog(config: LaunchConfig) -> None:
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
        run(["sudo", "install", "-m", mode, str(src), dst])

    env_file = "/etc/chutes/benchmark-netlog.env"
    if not os.path.exists(env_file):
        run(["sudo", "mkdir", "-p", "/etc/chutes"])
        content = f"BRIDGE_SUBNET={config.network.bridge_ip}\nNETLOG_DIR=/var/log/chutes/benchmark-netlog\n"
        proc.run(
            ["sudo", "tee", env_file],
            input=content.encode(),
            stdout=proc.DEVNULL,
            check=True,
        )
    run(["sudo", "systemctl", "daemon-reload"])
    proc.run(["sudo", "systemctl", "enable", "benchmark-netlog"], check=False)
    run(["sudo", "systemctl", "restart", "benchmark-netlog"])
    print("✓ benchmark-netlog service installed and running")
