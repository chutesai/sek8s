#!/usr/bin/env bash
# run-tdx.sh — launch Intel-TDX guest, auto-passthrough NVIDIA H200 GPUs and NVSwitches,
#              use preconfigured macvtap interface for networking.
# Enhanced for TDX + H200 compatibility with configurable memory settings and cloud-init support

# set -x

# Default values
IMG="guest-tools/image/tdx-guest-ubuntu-24.04-final.qcow2"
BIOS="/usr/share/ovmf/OVMF.fd"  # Replace with TDX-optimized OVMF
MEM="1536G"
VCPUS="24"
FOREGROUND=false
PIDFILE="/tmp/tdx-demo-td-pid.pid"
LOGFILE="/tmp/tdx-guest-td.log"
NET_IFACE=""  # Macvtap interface from setup-macvtap.sh
VM_IP=""  # VM static IP from setup-macvtap.sh
VM_GATEWAY=""  # VM gateway from setup-macvtap.sh
VM_DNS="8.8.8.8"  # Default DNS server
SSH_PORT=2222  # Host port for SSH (maps to VM:22)

# Cloud-init variables
USER_DATA=""  # Path to user-data YAML file (optional, overrides generated)
META_DATA=""  # Path to meta-data YAML file (optional)
CIDATA_FILE="/tmp/tdx-cidata.iso"  # Temporary file for cloud-init datasource
HOSTNAME=""  # Generated user-data param
MINER_SS58=""  # Generated user-data param
MINER_SEED=""  # Generated user-data param
GENERATED_USER_DATA="/tmp/generated-user-data.yaml"  # Temp file if generating user-data

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
    --cloud-init-user-data)
      USER_DATA="$2"
      shift 2
      ;;
    --cloud-init-meta-data)
      META_DATA="$2"
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
          echo "For SSH, use: ssh -p $SSH_PORT root@<host_public_ip> (e.g., 35.130.230.1)"
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
      rm -f "$CIDATA_FILE" "$GENERATED_USER_DATA"
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
      echo "  --net-iface IFACE         Macvtap interface from setup-macvtap.sh"
      echo "  --vm-ip IP                VM static IP from setup-macvtap.sh"
      echo "  --vm-gateway IP           VM gateway from setup-macvtap.sh"
      echo "  --vm-dns IP               VM DNS server (default: $VM_DNS)"
      echo "  --ssh-port PORT           Host port for SSH forwarding (default: $SSH_PORT)"
      echo "  --hostname NAME           VM hostname for generated user-data"
      echo "  --miner-ss58 VALUE        Content for /root/miner-ss58 file"
      echo "  --miner-seed VALUE        Content for /root/miner-seed file"
      echo "  --cloud-init-user-data FILE  Custom user-data YAML (overrides generated)"
      echo "  --cloud-init-meta-data FILE  Custom meta-data YAML (optional)"
      echo "  --status                  Show VM status"
      echo "  --clean                   Stop and clean VM"
      echo "  --help                    Show this help"
      echo ""
      echo "Example for conservative testing:"
      echo "  $0 --gpu-mmio-mb 16384 --pci-hole-base-gb 1024"
      echo ""
      echo "Example with macvtap networking:"
      echo "  $0 --hostname chutes-miner --miner-ss58 'actual_ss58' --miner-seed 'actual_seed' --net-iface vmnet-12345678 --vm-ip 192.168.100.2 --vm-gateway 192.168.100.1 --ssh-port 2222"
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
if [ -z "$NET_IFACE" ] || [ -z "$VM_IP" ] || [ -z "$VM_GATEWAY" ]; then
  echo "Error: --net-iface, --vm-ip, and --vm-gateway are required."
  exit 1
fi
if [ -z "$USER_DATA" ] && { [ -z "$HOSTNAME" ] || [ -z "$MINER_SS58" ] || [ -z "$MINER_SEED" ]; }; then
  echo "Error: --hostname, --miner-ss58, and --miner-seed are required unless --cloud-init-user-data is provided."
  exit 1
fi

# Generate user-data if params provided and no custom user-data
if [ -z "$USER_DATA" ]; then
  cat > "$GENERATED_USER_DATA" <<EOF
#cloud-config
hostname: $HOSTNAME

write_files:
  - path: /root/miner-ss58
    content: "$MINER_SS58"
    permissions: '0600'
    owner: root:root
  - path: /root/miner-seed
    content: "$MINER_SEED"
    permissions: '0600'
    owner: root:root
network:
  version: 2
  ethernets:
    enp0s1:
      addresses:
        - $VM_IP/24
      gateway4: $VM_GATEWAY
      nameservers:
        addresses: [$VM_DNS]
EOF
  USER_DATA="$GENERATED_USER_DATA"
elif [ -n "$USER_DATA" ] && { [ -n "$HOSTNAME" ] || [ -n "$MINER_SS58" ] || [ -n "$MINER_SEED" ]; }; then
  echo "Warning: --hostname, --miner-ss58, --miner-seed ignored since custom --cloud-init-user-data provided."
fi

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

echo "=== Device Detection ==="
echo "Found NVIDIA devices:"
echo "  GPUs: ${GPUS[*]:-none} (count: $TOTAL_GPUS)"
echo "  NVSwitches: ${NVSW[*]:-none} (count: $TOTAL_NVSW)"
echo ""

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
echo ""

##############################################################################
# 2. build dynamic -device list
##############################################################################
DEV_OPTS=(
  -netdev tap,id=n0,ifname="$NET_IFACE",script=no,downscript=no
  -device virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56
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

# Cloud-init: Generate cidata ISO if user-data provided
if [ -n "$USER_DATA" ]; then
  if [ ! -f "$USER_DATA" ]; then
    echo "Error: User-data file $USER_DATA not found."
    exit 1
  fi

  # If meta-data file provided, use it; otherwise, generate minimal meta-data with dynamic instance-id
  if [ -n "$META_DATA" ]; then
    if [ ! -f "$META_DATA" ]; then
      echo "Error: Meta-data file $META_DATA not found."
      exit 1
    fi
    META_DATA_FILE="$META_DATA"
  else
    # Generate dynamic instance-id (requires uuidgen or fallback to timestamp)
    if command -v uuidgen >/dev/null 2>&1; then
      INSTANCE_ID=$(uuidgen)
    else
      INSTANCE_ID="tdx-guest-$(date +%s)"
    fi
    META_DATA_FILE="/tmp/tdx-meta-data.$$"
    echo "instance-id: $INSTANCE_ID" > "$META_DATA_FILE" || {
      echo "Error: Failed to write meta-data to $META_DATA_FILE."
      exit 1
    }
  fi

  # Generate NoCloud datasource ISO using cloud-localds
  if command -v cloud-localds >/dev/null 2>&1; then
    cloud-localds "$CIDATA_FILE" "$USER_DATA" "$META_DATA_FILE" || {
      echo "Error: Failed to generate cloud-init ISO. Ensure cloud-utils is installed and up-to-date."
      [ -z "$META_DATA" ] && rm -f "$META_DATA_FILE"
      exit 1
    }
  else
    echo "Error: cloud-localds not found. Ensure cloud-utils is installed."
    [ -z "$META_DATA" ] && rm -f "$META_DATA_FILE"
    exit 1
  fi

  # Clean up temporary meta-data file if generated
  [ -z "$META_DATA" ] && rm -f "$META_DATA_FILE"

  echo "Cloud-init datasource generated: $CIDATA_FILE"
  echo "Attaching to QEMU as secondary drive."

  # Add to DEV_OPTS (attach as read-only VirtIO drive)
  DEV_OPTS+=( -drive file="$CIDATA_FILE",if=virtio,format=raw,readonly=on )
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
echo "Network: macvtap interface $NET_IFACE"
echo "VM IP: $VM_IP, Gateway: $VM_GATEWAY, DNS: $VM_DNS"
if [ -n "$USER_DATA" ]; then
  echo "Cloud-init: Enabled with user-data from $USER_DATA"
  [ -n "$META_DATA" ] && echo "Cloud-init meta-data: $META_DATA" || echo "Cloud-init meta-data: Generated with dynamic instance-id"
else
  echo "Cloud-init: Disabled (no user-data provided)"
fi
echo "Access VM via:"
echo "  SSH: ssh -p $SSH_PORT root@<host_public_ip> (e.g., 35.130.230.1)"
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
  echo "  SSH: ssh -p $SSH_PORT root@<host_public_ip> (e.g., 35.130.230.1)"
  echo "  k3s API: <host_public_ip>:6443"
  echo "  k3s NodePorts: <host_public_ip>:30000-32767"
  echo ""
  echo "=== TDX H200 nvidia-smi Troubleshooting Notes ==="
  echo "Current memory settings:"
  echo "  - PCI hole: ${CALCULATED_PCI_HOLE_GB}GB"
  echo "  - GPU MMIO: ${GPU_MMIO_MB}MB per GPU"
  echo "  - NVSwitch MMIO: ${NVSWITCH_MMIO_MB}MB per switch"
  echo ""
  echo "If nvidia-smi shows 'No devices were found':"
  echo "1. Check lspci shows devices: lspci | grep NVIDIA"
  echo "2. Check dmesg for NVIDIA errors: dmesg | grep -i nvidia"
  echo "3. Try disabling GSP firmware: echo 'options nvidia NVreg_EnableGpuFirmware=0' >> /etc/modprobe.d/nvidia.conf"
  echo "4. Check driver version compatibility with TDX"
  echo "5. Try different memory settings:"
  echo "   - Lower if soft lockups: --gpu-mmio-mb 16384 --pci-hole-base-gb 1024"
  echo "   - Higher if BAR allocation fails: --gpu-mmio-mb 65536 --pci-hole-base-gb 4096"
  echo ""
  echo "Memory testing progression:"
  echo "  Conservative: --gpu-mmio-mb 16384 --pci-hole-base-gb 1024"
  echo "  Moderate:     --gpu-mmio-mb 32768 --pci-hole-base-gb 2048 (current)"
  echo "  Aggressive:   --gpu-mmio-mb 65536 --pci-hole-base-gb 4096"
  echo "  Maximum:      --gpu-mmio-mb 147456 --pci-hole-base-gb 8192"
  rm -f "$CIDATA_FILE" "$GENERATED_USER_DATA"
  exit 0
fi

# Clean up cidata file on exit (foreground mode)
trap 'rm -f "$CIDATA_FILE" "$GENERATED_USER_DATA"' EXIT