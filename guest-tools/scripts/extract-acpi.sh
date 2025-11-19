#!/usr/bin/env bash
set -euo pipefail

# Extract ACPI tables for TDX measurement using the same
# QEMU topology as the real run-vm.sh launch, but without
# attaching the encrypted guest image. This script should
# be run on the host *before* starting the real VM.

TDVF="firmware/TDVF.fd"
OUT_DIR="measure/acpi"

# Match run-vm.sh defaults unless overridden via env
MEM="${MEM:-1536G}"
VCPUS="${VCPUS:-24}"
NETWORK_TYPE="${NETWORK_TYPE:-user}"
NET_IFACE="${NET_IFACE:-}"
CONFIG_VOLUME="${CONFIG_VOLUME:-}"
CACHE_VOLUME="${CACHE_VOLUME:-}"
SSH_PORT="${SSH_PORT:-2222}"

# Memory / MMIO config (copied from run-vm.sh)
PCI_HOLE_BASE_GB=2048
GPU_MMIO_MB=262144
NVSWITCH_MMIO_MB=32768
PCI_HOLE_OVERHEAD_PER_GPU_GB=0
PCI_HOLE_OVERHEAD_PER_NVSWITCH_GB=0
PCI_HOLE_BUFFER_GB=256

mkdir -p "$OUT_DIR"

echo "=== Extracting ACPI tables for TDX measurement ==="
echo "TDVF:      $TDVF"
echo "MEM:       $MEM"
echo "VCPUS:     $VCPUS"
echo "NET TYPE:  ${NETWORK_TYPE:-<none>}"
echo "NET IFACE: ${NET_IFACE:-<none>}"
echo "CONFIG VOL:${CONFIG_VOLUME:-<none>}"
echo "CACHE VOL: ${CACHE_VOLUME:-<none>}"
echo

if [[ ! -s "$TDVF" ]]; then
  echo "ERROR: TDVF not found or empty at $TDVF"
  exit 1
fi

# Basic sanity if user wants tap networking
if [[ "${NETWORK_TYPE}" == "tap" && -z "${NET_IFACE}" ]]; then
  echo "ERROR: NETWORK_TYPE=tap requires NET_IFACE to be set"
  exit 1
fi

# CPU options (copied from run-vm.sh)
CPU_OPTS=( -cpu host -smp "cores=${VCPUS},threads=2,sockets=2" )

##############################################################################
# Device detection (copied from run-vm.sh)
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
echo "  GPUs:       ${GPUS[*]:-none} (count: $TOTAL_GPUS)"
echo "  NVSwitches: ${NVSW[*]:-none} (count: $TOTAL_NVSW)"
echo

##############################################################################
# Build -device list to match run-vm.sh topology (minus guest root disk)
##############################################################################
DEV_OPTS=()

# Network configuration (copied from run-vm.sh logic)
if [[ "$NETWORK_TYPE" == "tap" ]]; then
  DEV_OPTS+=(
    -netdev tap,id=n0,ifname="$NET_IFACE",script=no,downscript=no
    -device virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56
  )
elif [[ "$NETWORK_TYPE" == "user" ]]; then
  DEV_OPTS+=(
    -netdev user,id=n0,ipv6=off,hostfwd=tcp::"${SSH_PORT}"-:22,hostfwd=tcp::6443-:6443
    -device virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56
  )
fi

# vsock (same as run-vm.sh)
DEV_OPTS+=(
  -device vhost-vsock-pci,guest-cid=3
)

# GPU passthrough root-ports + vfio devices + fw_cfg MMIO hints
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

  # GPU passthrough vfio
  DEV_OPTS+=( -device vfio-pci,host=${GPUS[i]},bus=${id},addr=0x0,iommufd=iommufd0 )

  # Per-GPU 64-bit MMIO window hint for OVMF/TDVF
  DEV_OPTS+=( -fw_cfg name=opt/ovmf/X-PciMmio64Mb$((i+1)),string=${GPU_MMIO_MB} )

  echo "GPU $((i+1)): ${GPUS[i]} -> bus=${id}, MMIO64=${GPU_MMIO_MB}MB"

  ((port++,func++))
  if ((func==8)); then func=0; ((slot++)); fi
done

# Add NVSwitch devices (same logic as run-vm.sh)
for j in "${!NVSW[@]}"; do
  id="rp_nvsw$((j+1))" chassis=$(( TOTAL_GPUS + j + 1 ))
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

# Attach config volume (virtio drive) if provided
if [[ -n "$CONFIG_VOLUME" ]]; then
  if [[ ! -f "$CONFIG_VOLUME" ]]; then
    echo "ERROR: CONFIG_VOLUME=$CONFIG_VOLUME does not exist"
    exit 1
  fi
  DEV_OPTS+=( -drive file="$CONFIG_VOLUME",if=virtio,format=qcow2,readonly=on )
fi

# Attach cache volume if provided
if [[ -n "$CACHE_VOLUME" ]]; then
  if [[ ! -f "$CACHE_VOLUME" ]]; then
    echo "ERROR: CACHE_VOLUME=$CACHE_VOLUME does not exist"
    exit 1
  fi
  DEV_OPTS+=( -drive file="$CACHE_VOLUME",if=virtio,cache=none,format=qcow2 )
fi

echo
echo "=== Launching QEMU for ACPI dump (no guest root disk) ==="
echo

# Use KVM and the same memory-backend topology as run-vm.sh,
# but do NOT attach the encrypted guest root disk. We also do
# NOT need -object tdx-guest; ACPI comes from the machine model.
timeout 20 qemu-system-x86_64 \
  -accel kvm \
  -object memory-backend-memfd,id=ram0,size="$MEM" \
  -machine q35,kernel-irqchip=split,memory-backend=ram0,dumpdtb="$OUT_DIR/acpi-tables.dtb" \
  -m "$MEM" \
  "${CPU_OPTS[@]}" \
  -bios "$TDVF" \
  -vga none \
  -nodefaults \
  -nographic \
  -serial none \
  -monitor none \
  -object iommufd,id=iommufd0 \
  "${DEV_OPTS[@]}" \
  -no-reboot \
  -S

if [[ ! -s "$OUT_DIR/acpi-tables.dtb" ]]; then
  echo "ERROR: ACPI dump failed or produced empty file: $OUT_DIR/acpi-tables.dtb"
  exit 1
fi

echo "✓ ACPI dump complete: $OUT_DIR/acpi-tables.dtb"
