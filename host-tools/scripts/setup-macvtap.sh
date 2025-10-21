#!/usr/bin/env bash
# setup-macvtap.sh — Configure macvtap networking with a private subnet for a VM,
#                    forwarding specific ports from the host’s public IP.
# Designed for datacenter baremetal servers, using NAT to reuse host’s public IP.

set -x

# Default values
BRIDGE_NAME="br0"  # Local bridge for private subnet
BRIDGE_IP="192.168.100.1/24"  # Host IP on bridge
VM_IP="192.168.100.2/24"  # VM static IP
VM_DNS="8.8.8.8"  # VM DNS server
PUBLIC_IFACE="ens9f0np0"  # Host’s public interface (e.g., ens9f0np0 with 172.16.80.27)
SSH_PORT=2222  # Host port for SSH forwarding to VM:22
K3S_API_PORT=6443  # k3s API port
NODE_PORTS="30000-32767"  # k3s NodePort range

# Infer VM_GATEWAY from BRIDGE_IP
VM_GATEWAY="${BRIDGE_IP%/*}"  # Strip netmask (e.g., 192.168.100.1/24 → 192.168.100.1)

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --bridge-name)
      BRIDGE_NAME="$2"
      shift 2
      ;;
    --bridge-ip)
      BRIDGE_IP="$2"
      VM_GATEWAY="${BRIDGE_IP%/*}"  # Update VM_GATEWAY if BRIDGE_IP changes
      shift 2
      ;;
    --vm-ip)
      VM_IP="$2"
      shift 2
      ;;
    --vm-dns)
      VM_DNS="$2"
      shift 2
      ;;
    --public-iface)
      PUBLIC_IFACE="$2"
      shift 2
      ;;
    --clean)
      # Clean up iptables rules
      sudo iptables -t nat -D PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$SSH_PORT" -j DNAT --to-destination "${VM_IP%/*}:22" 2>/dev/null
      sudo iptables -t nat -D PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$K3S_API_PORT" -j DNAT --to-destination "${VM_IP%/*}:$K3S_API_PORT" 2>/dev/null
      sudo iptables -t nat -D PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$NODE_PORTS" -j DNAT --to-destination "${VM_IP%/*}" 2>/dev/null
      sudo iptables -D FORWARD -i "$BRIDGE_NAME" -o "$BRIDGE_NAME" -j ACCEPT 2>/dev/null
      sudo iptables -t nat -D POSTROUTING -s "${VM_IP%/*}" -o "$PUBLIC_IFACE" -j MASQUERADE 2>/dev/null
      # Remove macvtap interfaces attached to bridge
      for iface in $(ip link show | grep -o "vmnet-[^:]*" | grep -v "@"); do
        sudo ip link delete "$iface" 2>/dev/null
      done
      # Remove bridge
      sudo ip link delete "$BRIDGE_NAME" 2>/dev/null
      echo "Network setup cleaned."
      exit 0
      ;;
    --help)
      echo "Usage: $0 [options]"
      echo "Options:"
      echo "  --bridge-name NAME        Bridge name (default: $BRIDGE_NAME)"
      echo "  --bridge-ip IP/MASK       Bridge IP and netmask (default: $BRIDGE_IP)"
      echo "  --vm-ip IP/MASK           VM static IP and netmask (default: $VM_IP)"
      echo "  --vm-dns IP               VM DNS server (default: $VM_DNS)"
      echo "  --public-iface IFACE      Host’s public interface (default: $PUBLIC_IFACE)"
      echo "  --clean                   Remove bridge, macvtap, and iptables rules"
      echo "  --help                    Show this help"
      echo ""
      echo "Example:"
      echo "  $0 --bridge-ip 192.168.100.1/24 --vm-ip 192.168.100.2/24 --vm-dns 8.8.8.8 --public-iface ens9f0np0"
      echo "Output: NET_IFACE=<macvtap_interface> VM_IP=<vm_ip> VM_GATEWAY=<vm_gateway>"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

# Validate required commands
for cmd in ip iptables; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Error: $cmd not found. Install it (e.g., sudo apt install iproute2 iptables)."
    exit 1
  fi
done

# Create bridge
sudo ip link add name "$BRIDGE_NAME" type bridge || { echo "Error: Failed to create bridge $BRIDGE_NAME."; exit 1; }
sudo ip addr add "$BRIDGE_IP" dev "$BRIDGE_NAME" || { echo "Error: Failed to assign $BRIDGE_IP to $BRIDGE_NAME."; sudo ip link delete "$BRIDGE_NAME" 2>/dev/null; exit 1; }
sudo ip link set "$BRIDGE_NAME" up || { echo "Error: Failed to up $BRIDGE_NAME."; sudo ip link delete "$BRIDGE_NAME" 2>/dev/null; exit 1; }

# Enable IP forwarding
sudo sysctl -w net.ipv4.ip_forward=1 || { echo "Error: Failed to enable IP forwarding."; sudo ip link delete "$BRIDGE_NAME" 2>/dev/null; exit 1; }

# Create macvtap interface
NET_IFACE="vmnet-$(uuidgen | cut -c1-8)"
sudo ip link add link "$BRIDGE_NAME" name "$NET_IFACE" type macvtap mode bridge || { echo "Error: Failed to create macvtap $NET_IFACE on $BRIDGE_NAME."; sudo ip link delete "$BRIDGE_NAME" 2>/dev/null; exit 1; }
sudo ip link set "$NET_IFACE" up || { echo "Error: Failed to up $NET_IFACE."; sudo ip link delete "$NET_IFACE" 2>/dev/null; sudo ip link delete "$BRIDGE_NAME" 2>/dev/null; exit 1; }

# Setup iptables for NAT
sudo iptables -t nat -A PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$SSH_PORT" -j DNAT --to-destination "${VM_IP%/*}:22" || { echo "Error: Failed to set SSH iptables rule."; sudo ip link delete "$NET_IFACE" 2>/dev/null; sudo ip link delete "$BRIDGE_NAME" 2>/dev/null; exit 1; }
sudo iptables -t nat -A PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$K3S_API_PORT" -j DNAT --to-destination "${VM_IP%/*}:$K3S_API_PORT" || { echo "Error: Failed to set k3s API iptables rule."; sudo ip link delete "$NET_IFACE" 2>/dev/null; sudo ip link delete "$BRIDGE_NAME" 2>/dev/null; exit 1; }
sudo iptables -t nat -A PREROUTING -i "$PUBLIC_IFACE" -p tcp --dport "$NODE_PORTS" -j DNAT --to-destination "${VM_IP%/*}" || { echo "Error: Failed to set NodePort iptables rule."; sudo ip link delete "$NET_IFACE" 2>/dev/null; sudo ip link delete "$BRIDGE_NAME" 2>/dev/null; exit 1; }
sudo iptables -A FORWARD -i "$BRIDGE_NAME" -o "$BRIDGE_NAME" -j ACCEPT || { echo "Error: Failed to set FORWARD iptables rule."; sudo ip link delete "$NET_IFACE" 2>/dev/null; sudo ip link delete "$BRIDGE_NAME" 2>/dev/null; exit 1; }
sudo iptables -t nat -A POSTROUTING -s "${VM_IP%/*}" -o "$PUBLIC_IFACE" -j MASQUERADE || { echo "Error: Failed to set MASQUERADE iptables rule."; sudo ip link delete "$NET_IFACE" 2>/dev/null; sudo ip link delete "$BRIDGE_NAME" 2>/dev/null; exit 1; }

# Output for run-tdx.sh
echo "NET_IFACE=$NET_IFACE"
echo "VM_IP=${VM_IP%/*}"
echo "VM_GATEWAY=$VM_GATEWAY"

exit 0