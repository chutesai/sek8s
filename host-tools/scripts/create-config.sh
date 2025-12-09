#!/usr/bin/env bash
# create-config-volume.sh - Create and populate a config volume for TDX VMs
# Usage: ./create-config.sh <output-path> <hostname> <miner-ss58> <miner-seed> <vm-ip> <vm-gateway> [vm-dns]
# Example: ./create-config.sh config.qcow2 chutes-miner "ss58_value" "seed_value" 192.168.100.2 192.168.100.1

set -euo pipefail

# Max 16 chars for filesystem label
LABEL="tdx-config"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_error() {
    echo -e "${RED}ERROR: $1${NC}" >&2
}

print_success() {
    echo -e "${GREEN}SUCCESS: $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}WARNING: $1${NC}"
}

print_info() {
    echo -e "$1"
}

ensure_parent_directory() {
    local target_path="$1"
    local parent_dir
    parent_dir=$(dirname "$target_path")

    if [[ "$parent_dir" == "." ]] || [[ -z "$parent_dir" ]]; then
        return 0
    fi

    if [ ! -d "$parent_dir" ]; then
        print_info "Creating directory: $parent_dir"
        if ! mkdir -p "$parent_dir"; then
            print_error "Failed to create directory: $parent_dir"
            exit 1
        fi
    fi
}

# Check for help flag
if [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
    cat << EOF
Usage: $0 <output-path> <hostname> <miner-ss58> <miner-seed> <vm-ip> <vm-gateway> [vm-dns]

Create and populate a config volume for TDX VMs with the required label.

Arguments:
  output-path    Path where the qcow2 file will be created
  hostname       VM hostname
  miner-ss58     Miner SS58 credential
  miner-seed     Miner seed credential  
  vm-ip          VM IP address
  vm-gateway     VM gateway IP
  vm-dns         VM DNS server (optional, default: 8.8.8.8)

Examples:
  $0 config.qcow2 chutes-miner "5abc..." "seed123" 192.168.100.2 192.168.100.1
  $0 /path/to/config.qcow2 my-miner "5def..." "seed456" 192.168.100.3 192.168.100.1 1.1.1.1

The volume will contain:
  /hostname         - VM hostname
  /miner-ss58       - Miner SS58 credential
  /miner-seed       - Miner seed credential
  /network-config.yaml - Netplan network configuration

The volume will be formatted with:
  - Filesystem: ext4
  - Label: $LABEL (required by TDX VMs)
  - Size: 10M (small, just for config files)

Requirements:
  - qemu-img (for creating qcow2 images)
  - qemu-nbd (for mounting qcow2 as block device)
  - mkfs.ext4 (for formatting)
  - Root/sudo access (for NBD operations)
  - NBD kernel module loaded
EOF
    exit 0
fi

# Validate arguments
if [ $# -lt 6 ] || [ $# -gt 7 ]; then
    print_error "Invalid number of arguments"
    echo "Usage: $0 <output-path> <hostname> <miner-ss58> <miner-seed> <vm-ip> <vm-gateway> [vm-dns]"
    echo "Example: $0 config.qcow2 chutes-miner 'ss58_value' 'seed_value' 192.168.100.2 192.168.100.1"
    echo "Run '$0 --help' for more information"
    exit 1
fi

OUTPUT_PATH="$1"
HOSTNAME="$2"
MINER_SS58="$3"
MINER_SEED="$4"
VM_IP="$5"
VM_GATEWAY="$6"
VM_DNS="${7:-8.8.8.8}"

# Basic validation
if [[ -z "$HOSTNAME" || -z "$MINER_SS58" || -z "$MINER_SEED" || -z "$VM_IP" || -z "$VM_GATEWAY" ]]; then
    print_error "All arguments except vm-dns are required and cannot be empty"
    exit 1
fi

# Validate hostname (basic check)
if ! [[ "$HOSTNAME" =~ ^[a-zA-Z0-9-]+$ ]]; then
    print_error "Invalid hostname format: $HOSTNAME"
    echo "Hostname must contain only letters, numbers, and hyphens"
    exit 1
fi

# Check if output file already exists
if [ -f "$OUTPUT_PATH" ]; then
    print_error "Output file already exists: $OUTPUT_PATH"
    echo "Please remove it first or choose a different path"
    exit 1
fi

ensure_parent_directory "$OUTPUT_PATH"

# Check for required commands
for cmd in qemu-img qemu-nbd mkfs.ext4 blkid; do
    if ! command -v "$cmd" &> /dev/null; then
        print_error "Required command not found: $cmd"
        case "$cmd" in
            qemu-img|qemu-nbd)
                echo "Install QEMU tools: sudo apt-get install qemu-utils"
                ;;
            mkfs.ext4|blkid)
                echo "Install filesystem tools: sudo apt-get install e2fsprogs"
                ;;
        esac
        exit 1
    fi
done

# Check if running as root (needed for NBD operations)
if [ "$EUID" -ne 0 ]; then
    print_error "This script must be run with sudo/root privileges"
    echo "Try: sudo $0 \"$OUTPUT_PATH\" \"$HOSTNAME\" \"$MINER_SS58\" \"$MINER_SEED\" \"$VM_IP\" \"$VM_GATEWAY\" \"$VM_DNS\""
    exit 1
fi

# Check if NBD module is loaded
if ! lsmod | grep -q '^nbd\s'; then
    print_info "Loading NBD kernel module..."
    if ! modprobe nbd max_part=8; then
        print_error "Failed to load NBD kernel module"
        echo "Ensure the nbd module is available in your kernel"
        exit 1
    fi
    print_success "NBD module loaded"
fi

# Find available NBD device
NBD_DEVICE=""
for i in {0..15}; do
    if [ -b "/dev/nbd$i" ] && ! qemu-nbd --list 2>/dev/null | grep -q "nbd$i"; then
        NBD_DEVICE="/dev/nbd$i"
        break
    fi
done

if [ -z "$NBD_DEVICE" ]; then
    print_error "No available NBD device found"
    echo "All NBD devices are in use. Try disconnecting unused devices:"
    echo "  sudo qemu-nbd --disconnect /dev/nbd0"
    exit 1
fi

print_info "Using NBD device: $NBD_DEVICE"

# Cleanup function
cleanup() {
    if [ -n "$NBD_DEVICE" ]; then
        print_info "Cleaning up NBD connection..."
        qemu-nbd --disconnect "$NBD_DEVICE" &> /dev/null || true
    fi
}

trap cleanup EXIT

# Step 1: Create small qcow2 image (config files are tiny)
print_info ""
print_info "Step 1/5: Creating qcow2 config volume..."
print_info "  Path: $OUTPUT_PATH"
print_info "  Size: 10M"

if ! qemu-img create -f qcow2 "$OUTPUT_PATH" 10M; then
    print_error "Failed to create qcow2 image"
    exit 1
fi

print_success "qcow2 image created"

# Step 2: Connect to NBD
print_info ""
print_info "Step 2/5: Connecting to NBD device..."

if ! qemu-nbd --connect="$NBD_DEVICE" "$OUTPUT_PATH"; then
    print_error "Failed to connect qcow2 to NBD device"
    rm -f "$OUTPUT_PATH"
    exit 1
fi

# Wait for device to be ready
sleep 1

if [ ! -b "$NBD_DEVICE" ]; then
    print_error "NBD device not available after connection"
    exit 1
fi

print_success "Connected to $NBD_DEVICE"

# Step 3: Format with ext4 and label
print_info ""
print_info "Step 3/5: Formatting with ext4..."
print_info "  Label: $LABEL"

if ! mkfs.ext4 -L "$LABEL" "$NBD_DEVICE"; then
    print_error "Failed to format device"
    exit 1
fi

print_success "Formatted with ext4"

# Step 4: Mount and populate with config files
print_info ""
print_info "Step 4/5: Populating config files..."

MOUNT_DIR="/tmp/tdx-config-mount-$$"
mkdir -p "$MOUNT_DIR"

if ! mount "$NBD_DEVICE" "$MOUNT_DIR"; then
    print_error "Failed to mount config volume"
    rmdir "$MOUNT_DIR" 2>/dev/null || true
    exit 1
fi

# Create hostname file
echo "$HOSTNAME" > "$MOUNT_DIR/hostname"
print_info "  ✓ Created hostname: $HOSTNAME"

# Create miner credential files  
echo "$MINER_SS58" > "$MOUNT_DIR/miner-ss58"
echo "$MINER_SEED" > "$MOUNT_DIR/miner-seed"
print_info "  ✓ Created miner credential files"

# Create network configuration
cat > "$MOUNT_DIR/network-config.yaml" << EOF
network:
  version: 2
  ethernets:
    any-ethernet:
      match:
        name: "en*"
      addresses:
        - ${VM_IP}/24
      routes:
        - to: default
          via: ${VM_GATEWAY}
      nameservers:
        addresses:
          - ${VM_DNS}
EOF
print_info "  ✓ Created network config: ${VM_IP} via ${VM_GATEWAY}"

# Set proper permissions
chmod 644 "$MOUNT_DIR/hostname" "$MOUNT_DIR/network-config.yaml"
chmod 600 "$MOUNT_DIR/miner-ss58" "$MOUNT_DIR/miner-seed"

# Sync and unmount
sync
umount "$MOUNT_DIR"
rmdir "$MOUNT_DIR"

print_success "Config files created and volume unmounted"

# Step 5: Verify
print_info ""
print_info "Step 5/5: Verifying volume..."

FS_INFO=$(blkid -o export "$NBD_DEVICE" 2>/dev/null || true)
FS_TYPE=$(echo "$FS_INFO" | grep '^TYPE=' | cut -d= -f2 || echo "unknown")
FS_LABEL=$(echo "$FS_INFO" | grep '^LABEL=' | cut -d= -f2 || echo "none")

if [ "$FS_TYPE" != "ext4" ]; then
    print_error "Filesystem type verification failed: expected ext4, got $FS_TYPE"
    exit 1
fi

if [ "$FS_LABEL" != "$LABEL" ]; then
    print_error "Label verification failed: expected $LABEL, got $FS_LABEL"
    exit 1
fi

print_success "Volume verified successfully"

# Disconnect NBD (will also happen in cleanup trap)
qemu-nbd --disconnect "$NBD_DEVICE" &> /dev/null

print_info ""
print_success "Config volume created successfully!"
print_info ""
print_info "Volume details:"
print_info "  Path: $OUTPUT_PATH"
print_info "  Size: 10M"
print_info "  Filesystem: ext4"
print_info "  Label: $LABEL"
print_info ""
print_info "Config files:"
print_info "  /hostname         : $HOSTNAME"
print_info "  /miner-ss58       : [credential file]"
print_info "  /miner-seed       : [credential file]"
print_info "  /network-config.yaml : ${VM_IP} via ${VM_GATEWAY}"
print_info ""
print_info "To use with run-vm.sh:"
print_info "  ./run-vm.sh --config-volume $OUTPUT_PATH [other options...]"
print_info ""
print_info "To verify the volume contents later:"
print_info "  sudo qemu-nbd --connect=/dev/nbd0 $OUTPUT_PATH"
print_info "  sudo mkdir /mnt/verify && sudo mount /dev/nbd0 /mnt/verify"
print_info "  sudo ls -la /mnt/verify/"
print_info "  sudo umount /mnt/verify && sudo qemu-nbd --disconnect /dev/nbd0"

exit 0