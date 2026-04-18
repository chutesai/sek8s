#!/usr/bin/env python3
"""Derive vm_ip and bridge_ip from a CIDR block.

Usage: python3 derive_cidr_net.py <cidr>
  e.g. python3 derive_cidr_net.py 192.168.50.0/24

Prints JSON: {"vm_ip": "192.168.50.2", "bridge_ip": "192.168.50.1/24"}
"""
import ipaddress
import json
import sys

cidr = sys.argv[1] if len(sys.argv) > 1 else ""
if not cidr:
    print("Usage: derive_cidr_net.py <cidr>", file=sys.stderr)
    sys.exit(1)

n = ipaddress.ip_network(cidr, strict=False)
print(json.dumps({
    "vm_ip": str(n.network_address + 2),
    "bridge_ip": f"{n.network_address + 1}/{n.prefixlen}",
}))
