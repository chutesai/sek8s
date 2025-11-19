#!/usr/bin/env bash
# run-vm.sh — launch Intel-TDX guest with volume-based configuration
# Enhanced for TDX compatibility with configurable memory settings

# Default values
IMG="../../guest-tools/image/tdx-guest-ubuntu-24.04-final.qcow2"
BIOS="../../firmware/TDVF.fd"
MEM="1536G"
VCPUS="24"
FOREGROUND=false
PIDFILE="/tmp/tdx-td-pid.pid"
LOGFILE="/tmp/tdx-guest-td.log"
NET_IFACE=""
SSH_PORT=2222
NETWORK_TYPE=""
CACHE_VOLUME=""
CONFIG_VOLUME=""

# ======================================================================
# MEMORY CONFIGURATION VARIABLES
# ======================================================================
PCI_HOLE_BASE_GB=2048
GPU_MMIO_MB=262144
NVSWITCH_MMIO_MB=32768
PCI_HOLE_OVERHEAD_PER_GPU_GB=0
PCI_HOLE_OVERHEAD_PER_NVSWITCH_GB=0
PCI_HOLE_BUFFER_GB=256  # Increased from 128 for better alignment

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --image) IMG="$2"; shift 2 ;;
    --vcpus) VCPUS="$2"; shift 2 ;;
    --mem) MEM="$2"; shift 2 ;;
    --gpu-mmio-mb) GPU_MMIO_MB="$2"; shift 2 ;;
    --nvswitch-mmio-mb) NVSWITCH_MMIO_MB="$2"; shift 2 ;;
    --pci-hole-base-gb) PCI_HOLE_BASE_GB="$2"; shift 2 ;;
    --foreground) FOREGROUND=true; shift ;;
    --network-type) NETWORK_TYPE="$2"; shift 2 ;;
    --net-iface) NET_IFACE="$2"; shift 2 ;;
    --ssh-port) SSH_PORT="$2"; shift 2 ;;
    --cache-volume) CACHE_VOLUME="$2"; shift 2 ;;
    --config-volume) CONFIG_VOLUME="$2"; shift 2 ;;
    --status)
      if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        if ps -p "$PID" > /dev/null; then
          echo "QEMU VM is running with PID: $PID"
          echo "Access: ssh -p $SSH_PORT root@<host_public_ip>"
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
      echo "VM cleaned."
      exit 0
      ;;
    --help)
      echo "Usage: $0 [options]"
      echo ""
      echo "Required:"
      echo "  --config-volume PATH      Config volume qcow2 (from create-config.sh)"
      echo "  --network-type TYPE       Network backend: tap, user"
      echo "  --net-iface IFACE         Network interface (for tap mode)"
      echo ""
      echo "Optional:"
      echo "  --cache-volume PATH       Cache volume qcow2 (from create-cache.sh)"
      echo "  --image PATH              Guest image path"
      echo "  --mem SIZE                Memory size (default: $MEM)"
      echo "  --vcpus NUM               Number of vCPUs (default: $VCPUS)"
      echo "  --foreground              Run in foreground"
      echo "  --gpu-mmio-mb SIZE        MMIO per GPU in MB (default: $GPU_MMIO_MB)"
      echo "  --nvswitch-mmio-mb SIZE   MMIO per NVSwitch in MB (default: $NVSWITCH_MMIO_MB)"
      echo "  --pci-hole-base-gb SIZE   Min PCI hole size in GB (default: $PCI_HOLE_BASE_GB)"
      echo ""
      echo "Management:"
      echo "  --status                  Show VM status"
      echo "  --clean                   Stop and clean VM"
      echo ""
      echo "Example:"
      echo "  $0 --config-volume config.qcow2 --network-type tap --net-iface vmtap0"
      exit 0
      ;;
    *) echo "Unknown option: $1. Use --help for usage."; exit 1 ;;
  esac
done

# Validate required parameters
if [ -z "$CONFIG_VOLUME" ]; then
  echo "Error: --config-volume is required."
  echo "Create one with: sudo ./create-config.sh config.qcow2 hostname ss58 seed vm-ip gateway"
  exit 1
fi

if [ -z "$NETWORK_TYPE" ]; then
  echo "Error: --network-type is required (tap, user)."
  exit 1
fi

if [ "$NETWORK_TYPE" = "tap" ] && [ -z "$NET_IFACE" ]; then
  echo "Error: --net-iface is required for tap networking."
  exit 1
fi

# Validate config volume
if [ ! -f "$CONFIG_VOLUME" ]; then
  echo "Error: Config volume not found: $CONFIG_VOLUME"
  exit 1
fi

if command -v qemu-img >/dev/null 2>&1; then
  CONFIG_FORMAT=$(qemu-img info "$CONFIG_VOLUME" 2>/dev/null | grep '^file format:' | awk '{print $3}')
  if [ "$CONFIG_FORMAT" != "qcow2" ]; then
    echo "Error: Config volume must be qcow2 format, got: $CONFIG_FORMAT"
    exit 1
  fi
fi

echo "✓ Config volume validated: $CONFIG_VOLUME"

# Validate cache volume if provided
if [ -n "$CACHE_VOLUME" ]; then
  if [ ! -f "$CACHE_VOLUME" ]; then
    echo "Error: Cache volume not found: $CACHE_VOLUME"
    exit 1
  fi
  echo "✓ Cache volume validated: $CACHE_VOLUME"
fi

CPU_OPTS=( -cpu host -smp "cores=${VCPUS},threads=2,sockets=2" )

##############################################################################
# Device detection
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

# Network configuration
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
fi

##############################################################################
# 2. build dynamic -device list
##############################################################################
DEV_OPTS+=(
  -device vhost-vsock-pci,guest-cid=3
)

# GPU passthrough
port=16 slot=0x3 func=0

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
  
  # GPU passthrough
  DEV_OPTS+=( -device vfio-pci,host=${GPUS[i]},bus=${id},addr=0x0,iommufd=iommufd0 )
  
  # Per-GPU 64-bit MMIO window
  DEV_OPTS+=( -fw_cfg name=opt/ovmf/X-PciMmio64Mb$((i+1)),string=${GPU_MMIO_MB} )
  
  echo "GPU $((i+1)): ${GPUS[i]} -> bus=${id}, MMIO64=${GPU_MMIO_MB}MB"
  
  ((port++,func++))
  if ((func==8)); then func=0; ((slot++)); fi
done

# Add NVSwitch devices
for j in "${!NVSW[@]}"; do
  id="rp_nvsw$((j+1))" chassis=$(( ${#GPUS[@]} + j + 1 ))
  if ((func==0)); then
    DEV_OPTS+=(
      -device pcie-root-port,port=${port},chassis=${chassis},id=${id},\
bus=pcie.0,multifunction=on,addr=$(printf 0x%x.0x%x "$slot" "$func")
    )
  else
    DEV_OPTS+=(
      -device pcie-root-port,port=${port},chassis=${chassis},id=${id},\
bus=pcie.0,addr=$(printf 0x%x.0x%x "$slot" "$func")
    )
  fi
  
  DEV_OPTS+=( -device vfio-pci,host=${NVSW[j]},bus=${id},addr=0x0,iommufd=iommufd0 )
  
  echo "NVSwitch $((j+1)): ${NVSW[j]} -> bus=${id}, MMIO64=${NVSWITCH_MMIO_MB}MB"
  
  ((port++,func++))
  if ((func==8)); then func=0; ((slot++)); fi
done

# Attach config volume as virtio drive
DEV_OPTS+=( -drive file="$CONFIG_VOLUME",if=virtio,format=qcow2,readonly=on )

# Add cache volume if provided
if [ -n "$CACHE_VOLUME" ]; then
  echo "Cache volume: $CACHE_VOLUME"
  echo "  Will be auto mounted at /var/snap by guest verification service"
  
  DEV_OPTS+=( -drive file="$CACHE_VOLUME",if=virtio,cache=none,format=qcow2 )
fi

if [ "$FOREGROUND" = true ]; then
  SERIAL_OPTS=( -serial mon:stdio )
else
  SERIAL_OPTS=( -serial file:"$LOGFILE" -daemonize -pidfile "$PIDFILE" )
fi

echo ""
echo "=== Starting TEE VM ==="
echo "Memory: $MEM, vCPUs: $VCPUS"
echo "Network: $NETWORK_TYPE ($NET_IFACE)"
echo "Config volume: $CONFIG_VOLUME"
if [ -n "$CACHE_VOLUME" ]; then
  echo "Cache volume: $CACHE_VOLUME"
fi
echo "Manual initialization: Enabled (no cloud-init)"
echo ""

# Launch QEMU
/usr/bin/qemu-system-x86_64 \
  -accel kvm \
  -object '{"qom-type":"tdx-guest","id":"tdx","quote-generation-socket":{"type": "vsock", "cid":"2","port":"4050"}}' \
  -object memory-backend-memfd,id=ram0,size="$MEM" \
  -machine q35,kernel-irqchip=split,confidential-guest-support=tdx,memory-backend=ram0 \
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
  echo "Access: ssh -p $SSH_PORT root@<host_public_ip>"
  echo "Logs: $LOGFILE"
fi
