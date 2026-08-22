#!/bin/bash
# teardown.sh — full teardown of a TEE VM environment (stop VM + bridge + benchmark-netlog).
#
# Invoked by `chutes-cvm down [config.yaml]` (cli.py _cmd_down). Loads the config (if given)
# so bridge cleanup uses the right PUBLIC_IFACE / BRIDGE_IP / VM_IP, stops the VM (via
# `chutes-cvm stop`), tears the bridge down, and stops the benchmark-netlog service.
#
# For a VM-only stop that LEAVES the shared bridge in place (e.g. the measurement capture
# VM), use `chutes-cvm stop` directly instead of this.
#
#   teardown.sh [config.yaml]
set -euo pipefail

CONFIG_FILE="${1:-}"

# Defaults mirror quick-launch.sh (used when no config / config omits them).
VM_IP="192.168.100.2"
BRIDGE_IP="192.168.100.1/24"
PUBLIC_IFACE=""

if [[ -n "$CONFIG_FILE" && -f "$CONFIG_FILE" ]]; then
  echo "Loading network config from: $CONFIG_FILE"
  # chutes-cvm config renders VM_IP / BRIDGE_IP / PUBLIC_IFACE (among others) as KEY=value.
  if CONFIG_OUTPUT=$(chutes-cvm config "$CONFIG_FILE" 2>/dev/null); then
    eval "$CONFIG_OUTPUT"
  else
    echo "⚠ Could not parse $CONFIG_FILE; using default network values for teardown." >&2
  fi
fi

# Resolve the public interface the same way quick-launch does: empty or a stale NIC name
# falls back to the default-route device so bridge --clean removes the right iptables rules.
if [[ -z "$PUBLIC_IFACE" ]] || ! ip link show "$PUBLIC_IFACE" >/dev/null 2>&1; then
  DETECTED_IFACE=$(ip -j route show default 2>/dev/null \
    | python3 -c "import json,sys; r=json.load(sys.stdin); print(r[0]['dev'] if r else '')" \
    2>/dev/null || true)
  [[ -n "$DETECTED_IFACE" ]] && PUBLIC_IFACE="$DETECTED_IFACE"
fi

echo "=== Cleaning Up TEE VM Environment ==="
echo "Stopping Chutes VM (if running)..."
chutes-cvm stop 2>/dev/null || true

echo "Waiting for VM processes to exit..."
for i in {1..15}; do
  if ! pgrep -f 'qemu-system|qemu-kvm|chutes-cvm' >/dev/null 2>&1; then
    echo "No VM processes found. Proceeding with bridge cleanup."
    break
  fi
  echo "VM processes still running; waiting... ($i/15)"
  sleep 1
done

./network/setup-bridge.sh --clean \
  --bridge-ip "$BRIDGE_IP" \
  --vm-ip "${VM_IP}/24" \
  --public-iface "$PUBLIC_IFACE" 2>/dev/null || true

if systemctl is-active --quiet benchmark-netlog 2>/dev/null; then
  echo "Stopping benchmark network logging service..."
  sudo systemctl stop benchmark-netlog
  echo "✓ benchmark-netlog stopped"
fi

echo "✓ Teardown complete"
