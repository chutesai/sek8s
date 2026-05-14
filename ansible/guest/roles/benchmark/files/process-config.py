#!/usr/bin/env python3
"""
process-config.py (benchmark) - Applies hostname and network configuration
from the config volume in benchmark VMs.

Only handles what benchmark needs: hostname + netplan. No miner credentials,
no Docker Hub, no k3s paths.
"""

import os
import re
import sys
import yaml
from datetime import datetime

CONFIG_MOUNT_DIR = "/var/config"
LOG_FILE = "/var/log/config-validator.log"

EXPECTED_FILES = {
    "hostname": "/var/config/hostname",
    "network-config.yaml": "/var/config/network-config.yaml",
}

HOSTNAME_TARGET = "/etc/hostname"
NETWORK_CONFIG_TARGET = "/etc/netplan/50-config-volume.yaml"


def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {level}: {message}"
    print(entry)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry + "\n")


def validate_hostname(hostname: str) -> tuple[bool, str]:
    if not isinstance(hostname, str):
        return False, "must be a string"
    hostname = hostname.strip()
    if len(hostname) > 63:
        return False, "too long (max 63 chars)"
    if not re.match(r'^[a-zA-Z0-9-]+$', hostname):
        return False, "contains invalid characters"
    if hostname.startswith('-') or hostname.endswith('-'):
        return False, "cannot start or end with hyphen"
    return True, "valid"


def validate_network_config(raw: str) -> tuple[bool, str]:
    try:
        content = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return False, f"invalid YAML: {e}"
    if not isinstance(content, dict):
        return False, "must be a dictionary"
    config = content.get("network", {})
    if config.get("version") != 2:
        return False, "must have version: 2"
    if "ethernets" not in config:
        return False, "must have 'ethernets' section"
    for iface, settings in config["ethernets"].items():
        if not isinstance(settings, dict):
            return False, f"settings for {iface} must be a dict"
        has_addresses = "addresses" in settings
        has_dhcp = settings.get("dhcp4") is True or settings.get("dhcp6") is True
        if not has_addresses and not has_dhcp:
            return False, f"{iface} must have addresses or DHCP"
    return True, "valid"


def read_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        log(f"Failed to read {path}: {e}", "ERROR")
        return None


def write_file(content: str, path: str, mode: int = 0o644) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(path, mode)
        log(f"Wrote {path}")
        return True
    except Exception as e:
        log(f"Failed to write {path}: {e}", "ERROR")
        return False


def clear_netplan() -> bool:
    try:
        netplan_dir = "/etc/netplan"
        if os.path.exists(netplan_dir):
            for name in os.listdir(netplan_dir):
                fp = os.path.join(netplan_dir, name)
                if os.path.isfile(fp):
                    os.remove(fp)
        else:
            os.makedirs(netplan_dir, exist_ok=True)
        log("Netplan directory cleared")
        return True
    except Exception as e:
        log(f"Failed to clear netplan: {e}", "ERROR")
        return False


def apply_config() -> bool:
    log("Starting benchmark config apply")

    if not os.path.ismount(CONFIG_MOUNT_DIR):
        log(f"Config volume not mounted at {CONFIG_MOUNT_DIR}", "ERROR")
        return False

    if not clear_netplan():
        return False

    for key, path in EXPECTED_FILES.items():
        if not os.path.isfile(path):
            log(f"Missing required config file: {path}", "ERROR")
            return False

    hostname = read_file(EXPECTED_FILES["hostname"])
    if hostname is None:
        return False
    ok, msg = validate_hostname(hostname)
    if not ok:
        log(f"Invalid hostname: {msg}", "ERROR")
        return False
    log(f"Hostname valid: {hostname}")

    network = read_file(EXPECTED_FILES["network-config.yaml"])
    if network is None:
        return False
    ok, msg = validate_network_config(network)
    if not ok:
        log(f"Invalid network config: {msg}", "ERROR")
        return False
    log("Network config valid")

    if not write_file(hostname + "\n", HOSTNAME_TARGET, 0o644):
        return False
    if not write_file(network, NETWORK_CONFIG_TARGET, 0o600):
        return False

    try:
        with open("/proc/sys/kernel/hostname", "w") as f:
            f.write(hostname)
        log(f"Hostname applied immediately: {hostname}")
    except Exception as e:
        log(f"Warning: could not set hostname immediately: {e}", "WARNING")

    log("Benchmark config applied successfully")
    return True


def main() -> None:
    if os.geteuid() != 0:
        log("Must be run as root", "ERROR")
        sys.exit(1)
    if apply_config():
        sys.exit(0)
    else:
        log("Config apply failed", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    main()
