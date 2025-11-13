#!/bin/bash
# setup-bridge-simple.sh - Simple, reliable bridge networking for VM
# Updated for true idempotency - reuses same TAP interface

set -e

# Default values
BRIDGE_NAME="br0"
BRIDGE_IP="192.168.100.1/24"
VM_IP="192.168.100.2/24"
VM_GATEWAY="192.168.100.1"
VM_DNS="8.8.8.8"
PUBLIC_IFACE="ens9f0np0"
SSH_PORT=2222
K3S_API_PORT=6443
NODE_PORTS="30000:32767"
TAP_IFACE="vmtap0"  # Fixed name for single VM per server

echo "=== Bridge Network Setup ==="
echo "Architecture: VM ← TAP ← Bridge ← NAT ← Internet"
echo

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --bridge-ip) BRIDGE_IP="$2"; VM_GATEWAY="${BRIDGE_IP%/*}"; shift 2 ;;
    --vm-ip) VM_IP="$2"; shift 2 ;;
    --vm-dns) VM_DNS="$2"; shift 2 ;;
    --public-iface) PUBLIC_IFACE="$2"; shift 2 ;;
    --clean)
      # Clean up everything
      sudo iptables -t nat -D PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$SSH_PORT" -j DNAT --to-destination "${VM_IP%/*}:22" 2>/dev/null || true
      sudo iptables -t nat -D PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$K3S_API_PORT" -j DNAT --to-destination "${VM_IP%/*}:$K3S_API_PORT" 2>/dev/null || true
      sudo iptables -t nat -D PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$NODE_PORTS" -j DNAT --to-destination "${VM_IP%/*}" 2>/dev/null || true
      sudo iptables -D FORWARD -i "$BRIDGE_NAME" -o "$PUBLIC_IFACE" -j ACCEPT 2>/dev/null || true
      sudo iptables -D FORWARD -i "$PUBLIC_IFACE" -o "$BRIDGE_NAME" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
      sudo iptables -t nat -D POSTROUTING -s "${BRIDGE_IP%/*}/24" -o "$PUBLIC_IFACE" -j MASQUERADE 2>/dev/null || true
      
      # Remove TAP interface
      sudo ip link delete "$TAP_IFACE" 2>/dev/null || true
      
      # Remove bridge
      sudo ip link delete "$BRIDGE_NAME" 2>/dev/null || true
      
      echo "Bridge network setup cleaned."
      exit 0
      ;;
    --help)
      echo "Usage: $0 [options]"
      echo "Simple bridge setup - reliable and well-tested"
      echo "Options:"
      echo "  --bridge-ip IP/MASK       Bridge IP (default: $BRIDGE_IP)"  
      echo "  --vm-ip IP/MASK           VM IP (default: $VM_IP)"
      echo "  --vm-dns IP               VM DNS (default: $VM_DNS)"
      echo "  --public-iface IFACE      Public interface (default: $PUBLIC_IFACE)"
      echo "  --clean                   Remove all bridge setup"
      echo "  --help                    Show this help"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "1. Creating bridge interface..."
# Check if bridge already exists
if ip link show "$BRIDGE_NAME" >/dev/null 2>&1; then
  echo "   ✓ Bridge $BRIDGE_NAME already exists"
  # Check if it has the right IP
  if ip addr show "$BRIDGE_NAME" | grep -q "${BRIDGE_IP%/*}"; then
    echo "   ✓ Bridge IP already configured: $BRIDGE_IP"
  else
    echo "   ⚠ Adding IP to existing bridge: $BRIDGE_IP"
    sudo ip addr add "$BRIDGE_IP" dev "$BRIDGE_NAME" 2>/dev/null || true
  fi
  # Ensure bridge is up
  sudo ip link set "$BRIDGE_NAME" up
else
  # Create new bridge
  sudo ip link add name "$BRIDGE_NAME" type bridge
  sudo ip addr add "$BRIDGE_IP" dev "$BRIDGE_NAME"
  sudo ip link set "$BRIDGE_NAME" up
  echo "   ✓ Bridge created: $BRIDGE_NAME with IP $BRIDGE_IP"
fi

echo "2. Setting up TAP interface for VM..."
# Check if TAP interface already exists and is properly configured
if ip link show "$TAP_IFACE" >/dev/null 2>&1; then
  echo "   ✓ TAP interface already exists: $TAP_IFACE"
  
  # Ensure it's up and connected to bridge
  sudo ip link set "$TAP_IFACE" up
  
  # Check if already connected to bridge
  if bridge link show | grep -q "$TAP_IFACE.*master $BRIDGE_NAME"; then
    echo "   ✓ TAP interface already connected to bridge"
  else
    echo "   ⚠ Connecting existing TAP interface to bridge"
    sudo ip link set "$TAP_IFACE" master "$BRIDGE_NAME"
  fi
else
  # Create new TAP interface
  sudo ip tuntap add dev "$TAP_IFACE" mode tap
  sudo ip link set "$TAP_IFACE" up
  sudo ip link set "$TAP_IFACE" master "$BRIDGE_NAME"
  echo "   ✓ TAP interface created and connected: $TAP_IFACE"
fi

echo "3. Setting up routing and NAT..."
sudo sysctl -w net.ipv4.ip_forward=1

# Port forwarding rules (idempotent - only add if not already present)
sudo iptables -t nat -C PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$SSH_PORT" -j DNAT --to-destination "${VM_IP%/*}:22" 2>/dev/null || \
  sudo iptables -t nat -A PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$SSH_PORT" -j DNAT --to-destination "${VM_IP%/*}:22"

sudo iptables -t nat -C PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$K3S_API_PORT" -j DNAT --to-destination "${VM_IP%/*}:$K3S_API_PORT" 2>/dev/null || \
  sudo iptables -t nat -A PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$K3S_API_PORT" -j DNAT --to-destination "${VM_IP%/*}:$K3S_API_PORT"

sudo iptables -t nat -C PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$NODE_PORTS" -j DNAT --to-destination "${VM_IP%/*}" 2>/dev/null || \
  sudo iptables -t nat -A PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$NODE_PORTS" -j DNAT --to-destination "${VM_IP%/*}"

# Traffic forwarding rules (idempotent)
sudo iptables -C FORWARD -i "$BRIDGE_NAME" -o "$PUBLIC_IFACE" -j ACCEPT 2>/dev/null || \
  sudo iptables -A FORWARD -i "$BRIDGE_NAME" -o "$PUBLIC_IFACE" -j ACCEPT

sudo iptables -C FORWARD -i "$PUBLIC_IFACE" -o "$BRIDGE_NAME" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  sudo iptables -A FORWARD -i "$PUBLIC_IFACE" -o "$BRIDGE_NAME" -m state --state RELATED,ESTABLISHED -j ACCEPT

# NAT for outbound traffic (idempotent)
sudo iptables -t nat -C POSTROUTING -s "${BRIDGE_IP%/*}/24" -o "$PUBLIC_IFACE" -j MASQUERADE 2>/dev/null || \
  sudo iptables -t nat -A POSTROUTING -s "${BRIDGE_IP%/*}/24" -o "$PUBLIC_IFACE" -j MASQUERADE

echo "   ✓ NAT and forwarding rules configured"

echo "4. Verifying configuration..."
echo "   Bridge status:"
ip link show "$BRIDGE_NAME" | sed 's/^/     /'

echo "   TAP interface status:"
ip link show "$TAP_IFACE" | sed 's/^/     /'

echo "   Bridge should have CARRIER and be UP:"
if ip link show "$BRIDGE_NAME" | grep -q "state UP"; then
    echo "   ✓ Bridge is operational"
else
    echo "   ⚠ Bridge may have issues"
fi

echo
echo "=== Bridge Setup Complete ==="
echo
echo "✓ Bridge-based networking configured"
echo
echo "For QEMU launch, use:"
echo "  --network-type tap"
echo "  --net-iface $TAP_IFACE"
echo "  --vm-ip ${VM_IP%/*}"
echo "  --vm-gateway $VM_GATEWAY"
echo
echo "Network interface: $TAP_IFACE"
echo "VM IP: ${VM_IP%/*}"
echo "VM Gateway: $VM_GATEWAY"
echo "Bridge IP: ${BRIDGE_IP%/*}"
echo
echo "SSH to VM: ssh -p $SSH_PORT root@<host_public_ip>"
echo "k3s API: <host_public_ip>:$K3S_API_PORT"

exit 0