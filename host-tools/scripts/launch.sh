#!/usr/bin/env bash
# run-tdx.sh — launch Intel-TDX guest, auto-passthrough NVIDIA H200 GPUs and NVSwitches,
#              use preconfigured network interface for manual initialization (no cloud-init)
# Enhanced for TDX compatibility with configurable memory settings and manual config

# Default values
IMG="guest-tools/image/tdx-guest-ubuntu-24.04-final.qcow2"
BIOS="/usr/share/ovmf/OVMF.fd"  # Replace with TDX-optimized OVMF
MEM="1536G"
VCPUS="24"
FOREGROUND=false
PIDFILE="/tmp/tdx-td-pid.pid"
LOGFILE="/tmp/tdx-guest-td.log"
NET_IFACE=""  # Network interface from setup script (e.g., tap)
VM_IP=""  # VM static IP from setup script
VM_GATEWAY=""  # VM gateway from setup script
VM_DNS="8.8.8.8"  # Default DNS server
SSH_PORT=2222  # Host port for SSH (maps to VM:22)
NETWORK_TYPE=""  # Network backend: tap, user (macvtap removed)
CACHE_VOLUME=""  # Path to cache volume qcow2 (optional)

# Manual initialization variables (replaces cloud-init)
CONFIG_FILE="/tmp/tee-config.iso"  # Generated config ISO
HOSTNAME=""  # VM hostname
MINER_SS58=""  # Content for miner-ss58 file
MINER_SEED=""  # Content for miner-seed file

# ======================================================================
# MEMORY CONFIGURATION VARIABLES - Adjust these for testing
# ======================================================================

# Base PCI hole size (in GB) - start conservative
PCI_HOLE_BASE_GB=2048

# Per-GPU MMIO allocation (in MB) 
# H200 has 141GB VRAM, but start smaller to avoid soft lockups
GPU_MMIO_MB=262144

# Per-NVSwitch MMIO allocation (in MB)
# Default: 32MB per switch, can increase if needed
NVSWITCH_MMIO_MB=32768

# PCI hole overhead per device (in GB) - additional space beyond MMIO
PCI_HOLE_OVERHEAD_PER_GPU_GB=0
PCI_HOLE_OVERHEAD_PER_NVSWITCH_GB=0

# ======================================================================

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --image)
      IMG="$2"
      shift 2
      ;;
    --vcpus)
      VCPUS="$2"
      shift 2
      ;;
    --mem)
      MEM="$2"
      shift 2
      ;;
    --gpu-mmio-mb)
      GPU_MMIO_MB="$2"
      shift 2
      ;;
    --nvswitch-mmio-mb)
      NVSWITCH_MMIO_MB="$2"
      shift 2
      ;;
    --pci-hole-base-gb)
      PCI_HOLE_BASE_GB="$2"
      shift 2
      ;;
    --foreground)
      FOREGROUND=true
      shift
      ;;
    --network-type)
      NETWORK_TYPE="$2"
      shift 2
      ;;
    --net-iface)
      NET_IFACE="$2"
      shift 2
      ;;
    --vm-ip)
      VM_IP="$2"
      shift 2
      ;;
    --vm-gateway)
      VM_GATEWAY="$2"
      shift 2
      ;;
    --vm-dns)
      VM_DNS="$2"
      shift 2
      ;;
    --ssh-port)
      SSH_PORT="$2"
      shift 2
      ;;
    --hostname)
      HOSTNAME="$2"
      shift 2
      ;;
    --miner-ss58)
      MINER_SS58="$2"
      shift 2
      ;;
    --miner-seed)
      MINER_SEED="$2"
      shift 2
      ;;
    --cache-volume)
      CACHE_VOLUME="$2"
      shift 2
      ;;
    --status)
      if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if ps -p "$PID" > /dev/null; then
          echo "QEMU VM is running with PID: $PID"
          if [ -f "$LOGFILE" ] && [ -s "$LOGFILE" ]; then
            echo "Recent serial log entries:"
            tail -n 5 "$LOGFILE"
          else
            echo "Serial log ($LOGFILE) is empty or missing."
          fi
          echo "For SSH, use: ssh -p $SSH_PORT root@<host_public_ip>"
          echo "For k3s, use: <host_public_ip>:6443 (API), <host_public_ip>:30000-32767 (NodePorts)"
        else
          echo "QEMU process ($PID) is not running."
        fi
      else
        echo "PID file not found. VM is likely not running."
      fi
      exit 0
      ;;
    --clean)
      if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if ps -p "$PID" > /dev/null; then
          echo "Terminating QEMU VM with PID: $PID"
          kill -TERM "$PID"
          for i in {1..5}; do
            if ! ps -p "$PID" > /dev/null; then
              echo "QEMU process terminated."
              break
            fi
            sleep 1
          done
          if ps -p "$PID" > /dev/null; then
            echo "QEMU process did not terminate gracefully, forcing kill."
            kill -9 "$PID"
          fi
        fi
        rm -f "$PIDFILE"
        echo "PID file removed."
      else
        echo "No PID file found. No VM to clean."
      fi
      rm -f "$CONFIG_FILE"
      exit 0
      ;;
    --help)
      echo "Usage: $0 [options]"
      echo "Options:"
      echo "  --image PATH              Guest image path"
      echo "  --vcpus NUM               Number of vCPUs"
      echo "  --mem SIZE                Memory size (e.g., 1536G)"
      echo "  --gpu-mmio-mb SIZE        MMIO allocation per GPU in MB (default: $GPU_MMIO_MB)"
      echo "  --nvswitch-mmio-mb SIZE   MMIO allocation per NVSwitch in MB (default: $NVSWITCH_MMIO_MB)"
      echo "  --pci-hole-base-gb SIZE   Base PCI hole size in GB (default: $PCI_HOLE_BASE_GB)"
      echo "  --foreground              Run in foreground"
      echo "  --network-type TYPE       Network backend: tap, user (required)"
      echo "  --net-iface IFACE         Network interface from bridge setup script"
      echo "  --vm-ip IP                VM static IP"
      echo "  --vm-gateway IP           VM gateway IP"
      echo "  --vm-dns IP               VM DNS server (default: $VM_DNS)"
      echo "  --ssh-port PORT           Host port for SSH forwarding (default: $SSH_PORT)"
      echo "  --hostname NAME           VM hostname"
      echo "  --miner-ss58 VALUE        Content for /root/miner-ss58 file"
      echo "  --miner-seed VALUE        Content for /root/miner-seed file"
      echo "  --cache-volume PATH       Path to cache volume qcow2 (optional)"
      echo "  --status                  Show VM status"
      echo "  --clean                   Stop and clean VM"
      echo "  --help                    Show this help"
      echo ""
      echo "Example workflow:"
      echo "  1. Setup bridge:   ./setup-bridge-simple.sh"
      echo "  2. Launch VM:      $0 --hostname chutes-miner --miner-ss58 'ss58' --miner-seed 'seed' --net-iface vmtap-XXXXX --vm-ip 192.168.100.2 --vm-gateway 192.168.100.1 --network-type tap"
      echo ""
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

# Validate required params
if [ -z "$NETWORK_TYPE" ]; then
  echo "Error: --network-type is required (tap, user)."
  exit 1
fi
if [ -z "$NET_IFACE" ] && [ "$NETWORK_TYPE" != "user" ]; then
  echo "Error: --net-iface is required for $NETWORK_TYPE."
  exit 1
fi
if [ "$NETWORK_TYPE" = "tap" ] && { [ -z "$VM_IP" ] || [ -z "$VM_GATEWAY" ]; }; then
  echo "Error: --vm-ip and --vm-gateway are required for tap networking."
  exit 1
fi
if [ -z "$HOSTNAME" ] || [ -z "$MINER_SS58" ] || [ -z "$MINER_SEED" ]; then
  echo "Error: --hostname, --miner-ss58, and --miner-seed are required for manual initialization."
  exit 1
fi

# Validate cache volume if provided
if [ -n "$CACHE_VOLUME" ]; then
  if [ ! -f "$CACHE_VOLUME" ]; then
    echo "Error: Cache volume file not found: $CACHE_VOLUME"
    exit 1
  fi
  
  if [ ! -r "$CACHE_VOLUME" ]; then
    echo "Error: Cache volume file not readable: $CACHE_VOLUME"
    exit 1
  fi
  
  # Verify it's a qcow2 file
  if command -v qemu-img >/dev/null 2>&1; then
    FILE_FORMAT=$(qemu-img info "$CACHE_VOLUME" 2>/dev/null | grep '^file format:' | awk '{print $3}')
    if [ "$FILE_FORMAT" != "qcow2" ]; then
      echo "Error: Cache volume is not in qcow2 format: $CACHE_VOLUME (detected: $FILE_FORMAT)"
      exit 1
    fi
    echo "Cache volume validated: $CACHE_VOLUME (format: qcow2)"
  fi
fi

# Generate config ISO for manual initialization
echo "=== Generating Configuration ISO ==="
CONFIG_DIR="/tmp/tee-config-$$"
mkdir -p "$CONFIG_DIR"

# Create hostname file
echo "$HOSTNAME" > "$CONFIG_DIR/hostname"
echo "Generated hostname config: $HOSTNAME"

# Create miner credential files
echo "$MINER_SS58" > "$CONFIG_DIR/miner-ss58"
echo "$MINER_SEED" > "$CONFIG_DIR/miner-seed"
echo "Generated miner credential files"

# Generate network configuration for tap networking
if [ "$NETWORK_TYPE" = "tap" ]; then
  cat > "$CONFIG_DIR/network-config.yaml" <<EOF
version: 2
ethernets:
  enp0s1:
    addresses:
      - ${VM_IP}/24
    routes:
      - to: default
        via: ${VM_GATEWAY}
    nameservers:
      addresses:
        - ${VM_DNS}
EOF
  echo "Generated network config: ${VM_IP} via ${VM_GATEWAY}"
elif [ "$NETWORK_TYPE" = "user" ]; then
  # User mode networking uses DHCP
  cat > "$CONFIG_DIR/network-config.yaml" <<EOF
version: 2
ethernets:
  enp0s1:
    dhcp4: true
EOF
  echo "Generated network config: DHCP (user mode)"
fi

# Create ISO using genisoimage
if command -v genisoimage >/dev/null 2>&1; then
  genisoimage -o "$CONFIG_FILE" -r -J "$CONFIG_DIR/" >/dev/null 2>&1 || {
    echo "Error: Failed to create configuration ISO"
    rm -rf "$CONFIG_DIR"
    exit 1
  }
  echo "Configuration ISO created: $CONFIG_FILE"
else
  echo "Error: genisoimage not found. Install with: sudo apt install genisoimage"
  rm -rf "$CONFIG_DIR"
  exit 1
fi

# Cleanup temp directory
rm -rf "$CONFIG_DIR"

CPU_OPTS=( -cpu host -smp "cores=${VCPUS},threads=2,sockets=2" )

##############################################################################
# 0. detect devices
##############################################################################
mapfile -t GPUS < <(
  lspci -Dn | awk '$2~/^(0300|0302):/ && $3~/^10de:/{print $1}' | sort
)
mapfile -t NVSW < <(
  lspci -Dn | awk '$2~/^0680:/ && $3~/^10de:22a3/{print $1}' | sort
)

TOTAL_GPUS=${#GPUS[@]}
TOTAL_NVSW=${#NVSW[@]}

echo
echo "=== Device Detection ==="
echo "Found NVIDIA devices:"
echo "  GPUs: ${GPUS[*]:-none} (count: $TOTAL_GPUS)"
echo "  NVSwitches: ${NVSW[*]:-none} (count: $TOTAL_NVSW)"
echo

##############################################################################
# 1. Calculate memory allocations
##############################################################################

# Calculate total PCI hole size needed
TOTAL_GPU_OVERHEAD_GB=$((TOTAL_GPUS * PCI_HOLE_OVERHEAD_PER_GPU_GB))
TOTAL_NVSWITCH_OVERHEAD_GB=$((TOTAL_NVSW * PCI_HOLE_OVERHEAD_PER_NVSWITCH_GB))
CALCULATED_PCI_HOLE_GB=$((PCI_HOLE_BASE_GB + TOTAL_GPU_OVERHEAD_GB + TOTAL_NVSWITCH_OVERHEAD_GB))

echo "=== Memory Configuration ==="
echo "Base PCI hole size: ${PCI_HOLE_BASE_GB}GB"
echo "GPU MMIO allocation: ${GPU_MMIO_MB}MB per GPU (${TOTAL_GPUS} GPUs)"
echo "NVSwitch MMIO allocation: ${NVSWITCH_MMIO_MB}MB per switch (${TOTAL_NVSW} switches)"
echo "GPU overhead: ${TOTAL_GPU_OVERHEAD_GB}GB (${PCI_HOLE_OVERHEAD_PER_GPU_GB}GB × ${TOTAL_GPUS})"
echo "NVSwitch overhead: ${TOTAL_NVSWITCH_OVERHEAD_GB}GB (${PCI_HOLE_OVERHEAD_PER_NVSWITCH_GB}GB × ${TOTAL_NVSW})"
echo "Calculated PCI hole size: ${CALCULATED_PCI_HOLE_GB}GB"
echo

##############################################################################
# 2. build dynamic -device list
##############################################################################
# Validate network type
case "$NETWORK_TYPE" in
  tap|user)
    ;;
  *)
    echo "Error: Invalid --network-type '$NETWORK_TYPE'. Use tap or user."
    exit 1
    ;;
esac

# Build network device options
DEV_OPTS=()
if [ "$NETWORK_TYPE" = "tap" ]; then
  if [ -z "$NET_IFACE" ]; then
    echo "Error: --net-iface must be specified for tap."
    exit 1
  fi
  DEV_OPTS+=(
    -netdev tap,id=n0,ifname="$NET_IFACE",script=no,downscript=no
    -device virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56
  )
elif [ "$NETWORK_TYPE" = "user" ]; then
  DEV_OPTS+=(
    -netdev user,id=n0,ipv6=off,hostfwd=tcp::"${SSH_PORT}"-:22,hostfwd=tcp::6443-:6443
    -device virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56
  )
  echo "Warning: User mode networking used. k3s NodePorts (30000-32767) not forwarded; configure manually if needed."
fi
DEV_OPTS+=(
  -device vhost-vsock-pci,guest-cid=3
)

port=16 slot=0x3 func=0

# Add GPU devices
for i in "${!GPUS[@]}"; do
  id="rp$((i+1))" chassis=$((i+1))
  if ((func==0)); then
    DEV_OPTS+=(
      -device pcie-root-port,port=${port},chassis=${chassis},id=${id},\
bus=pcie.0,multifunction=on,addr=$(printf 0x%x "$slot")
    )
  else
    DEV_OPTS+=(
      -device pcie-root-port,port=${port},chassis=${chassis},id=${id},\
bus=pcie.0,addr=$(printf 0x%x.0x%x "$slot" "$func")
    )
  fi
  
  # GPU passthrough - start with basic settings to avoid driver issues
  DEV_OPTS+=( -device vfio-pci,host=${GPUS[i]},bus=${id},addr=0x0,iommufd=iommufd0 )
  
  # Convert MB to bytes for fw_cfg (multiply by 1048576)
  DEV_OPTS+=( -fw_cfg name=opt/ovmf/X-PciMmio64Mb$((i+1)),string=${GPU_MMIO_MB} )
  
  echo "GPU $((i+1)): ${GPUS[i]} -> bus=${id}, MMIO=${GPU_MMIO_MB}MB"
  
  ((port++,func++))
  if ((func==8)); then func=0; ((slot++)); fi
done

# Add NVSwitch devices
for j in "${!NVSW[@]}"; do
  id="rp_nvsw$((j+1))" chassis=$(( ${#GPUS[@]} + j + 1 ))
  if ((func==0)); then
    DEV_OPTS+=(
      -device pcie-root-port,port=${port},chassis=${chassis},id=${id},bus=pcie.0,multifunction=on,addr=$(printf 0x%x.0x%x "$slot" "$func") )
  else
    DEV_OPTS+=(
      -device pcie-root-port,port=${port},chassis=${chassis},id=${id},\
bus=pcie.0,addr=$(printf 0x%x.0x%x "$slot" "$func") )
  fi
  
  DEV_OPTS+=( -device vfio-pci,host=${NVSW[j]},bus=${id},addr=0x0,iommufd=iommufd0 )
  
  echo "NVSwitch $((j+1)): ${NVSW[j]} -> bus=${id}, MMIO=${NVSWITCH_MMIO_MB}MB"
  
  ((port++,func++))
  if ((func==8)); then func=0; ((slot++)); fi
done

# Attach configuration ISO as virtio drive
DEV_OPTS+=( -drive file="$CONFIG_FILE",if=virtio,format=raw,readonly=on )

# Add cache volume if provided
if [ -n "$CACHE_VOLUME" ]; then
  echo "Cache volume: $CACHE_VOLUME"
  echo "  Will be auto mounted at /var/snap by guest verification service"
  
  # Attach cache volume as second virtio drive (vdb)
  DEV_OPTS+=( -drive file="$CACHE_VOLUME",if=virtio,cache=none,format=qcow2 )
fi

if [ "$FOREGROUND" = true ]; then
  SERIAL_OPTS=( -serial mon:stdio )
else
  SERIAL_OPTS=( -serial file:"$LOGFILE" -daemonize -pidfile "$PIDFILE" )
fi

echo ""
echo "=== Starting QEMU ==="
echo "Command preview:"
echo "PCI hole: ${CALCULATED_PCI_HOLE_GB}G"
echo "Memory: $MEM"
echo "vCPUs: $VCPUS"
echo "Network: $NETWORK_TYPE interface $NET_IFACE"
if [ "$NETWORK_TYPE" = "tap" ]; then
  echo "VM IP: $VM_IP, Gateway: $VM_GATEWAY, DNS: $VM_DNS"
else
  echo "VM IP: DHCP (user mode)"
fi
if [ -n "$CACHE_VOLUME" ]; then
  echo "Cache volume: Enabled ($CACHE_VOLUME -> /dev/vdb -> /var/snap)"
else
  echo "Cache volume: Not provided (optional)"
fi
echo "Manual initialization: Enabled ($CONFIG_FILE -> /dev/vdc)"
echo "Access VM via:"
if [ "$NETWORK_TYPE" = "tap" ]; then
  echo "  SSH: ssh -p $SSH_PORT root@<host_public_ip>"
else
  echo "  SSH: ssh -p $SSH_PORT root@localhost"
fi
echo "  k3s API: <host_public_ip>:6443"
echo "  k3s NodePorts: <host_public_ip>:30000-32767"
echo ""

/usr/bin/qemu-system-x86_64 \
  -accel kvm \
  -object '{"qom-type":"tdx-guest","id":"tdx","quote-generation-socket":{"type": "vsock", "cid":"2","port":"4050"}}' \
  -object memory-backend-memfd,id=ram0,size="$MEM" \
  -machine q35,kernel-irqchip=split,confidential-guest-support=tdx,memory-backend=ram0 \
  -global q35-pcihost.pci-hole64-size="${CALCULATED_PCI_HOLE_GB}G" \
  -m "$MEM" \
  "${CPU_OPTS[@]}" \
  -bios "$BIOS" \
  -drive file="$IMG",if=virtio \
  -vga none \
  -nodefaults \
  -nographic \
  "${SERIAL_OPTS[@]}" \
  -object iommufd,id=iommufd0 \
  -d int,guest_errors \
  -D /tmp/qemu.log \
  "${DEV_OPTS[@]}"

if [ "$FOREGROUND" = false ]; then
  echo "VM daemonized with PID: $(cat $PIDFILE)"
  echo "Serial log: $LOGFILE"
  echo "Access VM via:"
  if [ "$NETWORK_TYPE" = "tap" ]; then
    echo "  SSH: ssh -p $SSH_PORT root@<host_public_ip>"
  else
    echo "  SSH: ssh -p $SSH_PORT root@localhost"
  fi
  echo "  k3s API: <host_public_ip>:6443"
  echo "  k3s NodePorts: <host_public_ip>:30000-32767"
  echo ""
  echo "=== Manual Initialization Notes ==="
  echo "VM will automatically configure itself from $CONFIG_FILE"
  echo "No cloud-init - secure manual initialization only"
  echo "Check VM console logs if initialization fails"
  echo ""
  if [ -n "$CACHE_VOLUME" ]; then
    echo "=== Cache Volume Notes ==="
    echo "The cache volume will be verified and mounted at boot."
    echo "Check serial log if VM shuts down: cat $LOGFILE | grep -i cache"
    echo ""
  fi
  echo "=== TDX H200 nvidia-smi Troubleshooting Notes ==="
  echo "Current memory settings:"
  echo "  - PCI hole: ${CALCULATED_PCI_HOLE_GB}GB"
  echo "  - GPU MMIO: ${GPU_MMIO_MB}MB per GPU"
  echo "  - NVSwitch MMIO: ${NVSWITCH_MMIO_MB}MB per switch"
  echo ""
  echo "Memory testing progression:"
  echo "  Conservative: --gpu-mmio-mb 16384 --pci-hole-base-gb 1024"
  echo "  Moderate:     --gpu-mmio-mb 32768 --pci-hole-base-gb 2048 (current)"
  echo "  Aggressive:   --gpu-mmio-mb 65536 --pci-hole-base-gb 4096"
  echo "  Maximum:      --gpu-mmio-mb 147456 --pci-hole-base-gb 8192"
  rm -f "$CONFIG_FILE"
  exit 0
fi

# Clean up config file on exit (foreground mode)
trap 'rm -f "$CONFIG_FILE"' EXIT