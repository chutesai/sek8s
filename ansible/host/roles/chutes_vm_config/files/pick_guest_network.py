#!/usr/bin/env python3
"""Pick a /24 for the TEE guest bridge that does not overlap any host IPv4 network.

Reads `ip -j addr` and `ip -j route show default`, prints JSON:
  {"public_interface": "...", "vm_ip": "a.b.c.2", "bridge_ip": "a.b.c.1/24"}

Exit 1 if no suitable /24 is found or no usable public interface.
"""
from __future__ import annotations

import ipaddress
import json
import subprocess
import sys


def _run_json(argv: list[str]) -> object:
    out = subprocess.check_output(argv, text=True)
    return json.loads(out)


def host_ipv4_networks() -> list[ipaddress.IPv4Network]:
    nets: list[ipaddress.IPv4Network] = []
    for iface in _run_json(["ip", "-j", "addr"]):
        for a in iface.get("addr_info", []):
            if a.get("family") != "inet":
                continue
            local = a.get("local")
            plen = a.get("prefixlen")
            if not local or plen is None:
                continue
            iface_ip = ipaddress.ip_interface(f"{local}/{plen}")
            nets.append(iface_ip.network)
    return nets


def default_public_interface() -> str | None:
    try:
        routes = _run_json(["ip", "-j", "route", "show", "default"])
        if isinstance(routes, list) and routes:
            dev = routes[0].get("dev")
            if dev:
                return str(dev)
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        pass
    for iface in _run_json(["ip", "-j", "addr"]):
        name = iface.get("ifname", "")
        if not name or name == "lo":
            continue
        for a in iface.get("addr_info", []):
            if a.get("family") == "inet":
                return str(name)
    return None


def candidate_networks() -> list[ipaddress.IPv4Network]:
    out: list[ipaddress.IPv4Network] = []
    for third in range(100, 200):
        out.append(ipaddress.ip_network(f"192.168.{third}.0/24"))
    for second in range(200, 240):
        out.append(ipaddress.ip_network(f"10.{second}.0.0/24"))
    return out


def pick_internal(host_nets: list[ipaddress.IPv4Network]) -> ipaddress.IPv4Network:
    for cand in candidate_networks():
        if any(cand.overlaps(h) for h in host_nets):
            continue
        return cand
    sys.stderr.write(
        "chutes_vm_config: no non-overlapping /24 found in candidate ranges "
        "(192.168.100-199/24, 10.200-239/24). Set chutes_guest_bridge_network manually.\n"
    )
    raise SystemExit(1)


def main() -> None:
    host_nets = host_ipv4_networks()
    public_if = default_public_interface()
    if not public_if:
        sys.stderr.write(
            "chutes_vm_config: could not determine public/default-route interface. "
            "Set chutes_public_interface manually.\n"
        )
        raise SystemExit(1)
    internal = pick_internal(host_nets)
    # .1 bridge, .2 VM (matches host-tools defaults)
    bridge_host = internal.network_address + 1
    vm_host = internal.network_address + 2
    result = {
        "public_interface": public_if,
        "vm_ip": str(vm_host),
        "bridge_ip": f"{bridge_host}/24",
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
