#!/bin/bash
# quick-launch-tee.sh - Automated bridge setup and TEE VM launch

set -e

# Default values
HOSTNAME=""
MINER_SS58=""
MINER_SEED=""
VM_IP="192.168.100.2"
BRIDGE_IP="192.168.100.1/24"
VM_DNS="8.8.8.8"
PUBLIC_IFACE="ens9f0np0"
FOREGROUND=false
CACHE_VOLUME=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --hostname) HOSTNAME="$2"; shift 2 ;;
    --miner-ss58) MINER_SS58="$2"; shift 2 ;;
    --miner-seed) MINER_SEED="$2"; shift 2 ;;
    --vm-ip) VM_IP="$2"; shift 2 ;;
    --bridge-ip) BRIDGE_IP="$2"; shift 2 ;;
    --vm-dns) VM_DNS="$2"; shift 2 ;;
    --public-iface) PUBLIC_IFACE="$2"; shift 2 ;;
    --cache-volume) CACHE_VOLUME="$2"; shift 2 ;;
    --foreground) FOREGROUND=true; shift ;;
    --clean)
      echo "Cleaning up bridge and stopping VM..."
      ./run-tdx-manual.sh --clean 2>/dev/null || true
      ./setup-bridge-simple.sh --clean 2>/dev/null || true
      echo "Cleanup complete."
      exit 0
      ;;
    --help)
      echo "Usage: $0 [options]"
      echo "Automated bridge setup and TEE VM launch"
      echo ""
      echo "Required:"
      echo "  --hostname NAME           VM hostname"
      echo "  --miner-ss58 VALUE        Miner SS58 credential"
      echo "  --miner-seed VALUE        Miner seed credential"
      echo ""
      echo "Optional:"
      echo "  --vm-ip IP                VM IP (default: $VM_IP)"
      echo "  --bridge-ip IP/MASK       Bridge IP (default: $BRIDGE_IP)"
      echo "  --vm-dns IP               VM DNS (default: $VM_DNS)"
      echo "  --public-iface IFACE      Host public interface (default: $PUBLIC_IFACE)"
      echo "  --cache-volume PATH       Cache volume qcow2 file"
      echo "  --foreground              Run VM in foreground"
      echo "  --clean                   Clean up and stop everything"
      echo "  --help                    Show this help"
      echo ""
      echo "Example:"
      echo "  $0 --hostname chutes-miner --miner-ss58 'your_ss58' --miner-seed 'your_seed'"
      exit 0
      ;;
    *) echo "Unknown option: $1. Use --help for usage."; exit 1 ;;
  esac
done

# Validate required parameters
if [[ -z "$HOSTNAME" || -z "$MINER_SS58" || -z "$MINER_SEED" ]]; then
  echo "Error: --hostname, --miner-ss58, and --miner-seed are required."
  echo "Use --help for usage information."
  exit 1
fi

echo "=== TEE VM Quick Launch ==="
echo "Hostname: $HOSTNAME"
echo "VM IP: $VM_IP"
echo "Bridge IP: $BRIDGE_IP"
echo "Cache volume: ${CACHE_VOLUME:-Not provided}"
echo ""

# Step 1: Setup bridge networking
echo "Step 1: Setting up bridge networking..."
BRIDGE_OUTPUT=$(./setup-bridge-simple.sh \
  --bridge-ip "$BRIDGE_IP" \
  --vm-ip "${VM_IP}/24" \
  --vm-dns "$VM_DNS" \
  --public-iface "$PUBLIC_IFACE" 2>&1)

# Extract TAP interface name from bridge setup output
TAP_IFACE=$(echo "$BRIDGE_OUTPUT" | grep "Network interface:" | awk '{print $3}')
if [[ -z "$TAP_IFACE" ]]; then
  echo "Error: Failed to extract TAP interface from bridge setup."
  echo "Bridge setup output:"
  echo "$BRIDGE_OUTPUT"
  exit 1
fi

echo "✓ Bridge networking configured"
echo "✓ TAP interface: $TAP_IFACE"
echo ""

# Step 2: Launch TEE VM
echo "Step 2: Launching TEE VM..."
LAUNCH_ARGS=(
  --hostname "$HOSTNAME"
  --miner-ss58 "$MINER_SS58"
  --miner-seed "$MINER_SEED"
  --net-iface "$TAP_IFACE"
  --vm-ip "$VM_IP"
  --vm-gateway "${BRIDGE_IP%/*}"
  --vm-dns "$VM_DNS"
  --network-type tap
)

if [[ -n "$CACHE_VOLUME" ]]; then
  LAUNCH_ARGS+=(--cache-volume "$CACHE_VOLUME")
fi

if [[ "$FOREGROUND" = true ]]; then
  LAUNCH_ARGS+=(--foreground)
fi

./run-tdx-manual.sh "${LAUNCH_ARGS[@]}"

echo ""
echo "=== TEE VM Launch Complete ==="
echo ""
echo "VM Details:"
echo "  Hostname: $HOSTNAME"
echo "  IP Address: $VM_IP"
echo "  Bridge Gateway: ${BRIDGE_IP%/*}"
echo "  TAP Interface: $TAP_IFACE"
echo ""
echo "Access:"
echo "  SSH: ssh -p 2222 root@$(curl -s ifconfig.me)"
echo "  k3s API: $(curl -s ifconfig.me):6443"
echo ""
echo "Management:"
echo "  VM Status: ./run-tdx-manual.sh --status"
echo "  Stop VM: ./run-tdx-manual.sh --clean"
echo "  Clean All: $0 --clean"
echo ""
echo "Security:"
echo "  Manual initialization (no cloud-init)"
echo "  Bridge networking with NAT"
echo "  TEE attestation enabled"
if [[ -n "$CACHE_VOLUME" ]]; then
echo "  Cache volume mounted and verified"
fi

exit 0