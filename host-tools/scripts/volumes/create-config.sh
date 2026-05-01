#!/usr/bin/env bash
# create-config-volume.sh - Create or refresh a config qcow2 for TDX VMs
# Usage: ./create-config.sh <output-path> <hostname> <miner-ss58> <miner-seed> <vm-ip> <vm-gateway> [vm-dns] [docker-hub-user] [docker-hub-token]
# If output-path exists, the image is mounted and config files are replaced in place (stop QEMU if it holds the file open).
# Example: ./create-config.sh config.qcow2 chutes-miner "ss58_value" "seed_value" 192.168.100.2 192.168.100.1
# With Docker Hub (optional 8th/9th args, requires vm-dns as 7th): ... 8.8.8.8 "hubuser" "dckr_pat_..."

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

    if [[ -z "$parent_dir" ]] || [[ "$parent_dir" == "." ]]; then
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

populate_config_into_mount() {
    local MOUNT_DIR="$1"
    print_info "  Clearing all entries at volume root..."
    if ! find "$MOUNT_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +; then
        print_error "Failed to clear config volume"
        exit 1
    fi

    echo "$HOSTNAME" > "$MOUNT_DIR/hostname"
    print_info "  ✓ hostname: $HOSTNAME"

    if [[ -n "$MINER_SS58" && -n "$MINER_SEED" ]]; then
        echo "$MINER_SS58" > "$MOUNT_DIR/miner-ss58"
        echo "$MINER_SEED" > "$MOUNT_DIR/miner-seed"
        chmod 600 "$MOUNT_DIR/miner-ss58" "$MOUNT_DIR/miner-seed"
        print_info "  ✓ miner credential files"
    else
        print_info "  - miner credentials empty, skipping (benchmark mode)"
    fi

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
    print_info "  ✓ network-config.yaml (${VM_IP} via ${VM_GATEWAY})"

    chmod 644 "$MOUNT_DIR/hostname" "$MOUNT_DIR/network-config.yaml"

    if [[ -n "$DOCKER_HUB_USER" && -n "$DOCKER_HUB_TOKEN" ]]; then
        printf '%s' "$DOCKER_HUB_USER" > "$MOUNT_DIR/docker-hub-username"
        printf '%s' "$DOCKER_HUB_TOKEN" > "$MOUNT_DIR/docker-hub-token"
        chmod 600 "$MOUNT_DIR/docker-hub-username" "$MOUNT_DIR/docker-hub-token"
        print_info "  ✓ Docker Hub credential files"
    fi
}

# Check for help flag
if [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
    cat << EOF
Usage: $0 <output-path> <hostname> <miner-ss58> <miner-seed> <vm-ip> <vm-gateway> [vm-dns] [docker-hub-user] [docker-hub-token]

Create a new config qcow2, or refresh an existing one (same path, ext4 label $LABEL).

Arguments:
  output-path    qcow2 path (created if missing; updated in place if it already exists)
  hostname       VM hostname
  miner-ss58     Miner SS58 credential
  miner-seed     Miner seed credential  
  vm-ip          VM IP address
  vm-gateway     VM gateway IP
  vm-dns         VM DNS server (optional if only 6 base args; default: 8.8.8.8)
  docker-hub-user   Optional Docker Hub username (requires docker-hub-token and vm-dns)
  docker-hub-token  Optional Docker Hub PAT/password (requires docker-hub-user)

Examples:
  $0 config.qcow2 chutes-miner "5abc..." "seed123" 192.168.100.2 192.168.100.1
  $0 /path/to/config.qcow2 my-miner "5def..." "seed456" 192.168.100.3 192.168.100.1 1.1.1.1
  $0 config.qcow2 miner "5..." "seed..." 192.168.100.2 192.168.100.1 8.8.8.8 "dockeruser" "dckr_pat_xxx"

The volume will contain:
  /hostname         - VM hostname
  /miner-ss58       - Miner SS58 credential
  /miner-seed       - Miner seed credential
  /network-config.yaml - Netplan network configuration
  /docker-hub-username, /docker-hub-token - optional Docker Hub credentials (mode 0600)

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

# Validate arguments: 6 (no dns), 7 (with dns), or 9 (dns + docker hub pair)
if [ $# -lt 6 ] || [ $# -eq 8 ] || [ $# -gt 9 ]; then
    print_error "Invalid number of arguments"
    echo "Usage: $0 <output-path> <hostname> <miner-ss58> <miner-seed> <vm-ip> <vm-gateway> [vm-dns] [docker-hub-user] [docker-hub-token]"
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
DOCKER_HUB_USER=""
DOCKER_HUB_TOKEN=""

if [ $# -eq 6 ]; then
    VM_DNS="8.8.8.8"
elif [ $# -eq 7 ]; then
    VM_DNS="$7"
elif [ $# -eq 9 ]; then
    VM_DNS="$7"
    DOCKER_HUB_USER="$8"
    DOCKER_HUB_TOKEN="$9"
fi

# Basic validation
if [[ -z "$HOSTNAME" || -z "$MINER_SS58" || -z "$MINER_SEED" || -z "$VM_IP" || -z "$VM_GATEWAY" || -z "$VM_DNS" ]]; then
    print_error "Hostname, miner credentials, VM IP, gateway, and DNS must be non-empty"
    exit 1
fi

if [[ -n "$DOCKER_HUB_USER" || -n "$DOCKER_HUB_TOKEN" ]]; then
    if [[ -z "$DOCKER_HUB_USER" || -z "$DOCKER_HUB_TOKEN" ]]; then
        print_error "Docker Hub username and token must both be provided when using Hub auth"
        exit 1
    fi
fi

# Validate hostname (basic check)
if ! [[ "$HOSTNAME" =~ ^[a-zA-Z0-9-]+$ ]]; then
    print_error "Invalid hostname format: $HOSTNAME"
    echo "Hostname must contain only letters, numbers, and hyphens"
    exit 1
fi

EXISTING_VOLUME=false
if [ -f "$OUTPUT_PATH" ]; then
    EXISTING_VOLUME=true
elif [ -e "$OUTPUT_PATH" ]; then
    print_error "Path exists and is not a regular file: $OUTPUT_PATH"
    exit 1
else
    ensure_parent_directory "$OUTPUT_PATH"
fi

# Check for required commands
REQUIRED_CMDS=(qemu-nbd blkid)
if [ "$EXISTING_VOLUME" != true ]; then
    REQUIRED_CMDS=(qemu-img qemu-nbd mkfs.ext4 blkid)
fi
for cmd in "${REQUIRED_CMDS[@]}"; do
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

if [ "$EXISTING_VOLUME" = true ]; then
    print_info ""
    print_info "Updating existing config volume (replace files in place)..."
    print_info "  Path: $OUTPUT_PATH"
else
    print_info ""
    print_info "Step 1/5: Creating qcow2 config volume..."
    print_info "  Path: $OUTPUT_PATH"
    print_info "  Size: 10M"

    if ! qemu-img create -f qcow2 "$OUTPUT_PATH" 10M; then
        print_error "Failed to create qcow2 image"
        exit 1
    fi

    print_success "qcow2 image created"
fi

print_info ""
if [ "$EXISTING_VOLUME" = true ]; then
    print_info "Step 1/3: Connecting to NBD device..."
else
    print_info "Step 2/5: Connecting to NBD device..."
fi

if ! qemu-nbd --connect="$NBD_DEVICE" "$OUTPUT_PATH"; then
    print_error "Failed to connect qcow2 to NBD device"
    if [ "$EXISTING_VOLUME" != true ]; then
        rm -f "$OUTPUT_PATH"
    fi
    exit 1
fi

sleep 1

if [ ! -b "$NBD_DEVICE" ]; then
    print_error "NBD device not available after connection"
    exit 1
fi

print_success "Connected to $NBD_DEVICE"

if [ "$EXISTING_VOLUME" != true ]; then
    print_info ""
    print_info "Step 3/5: Formatting with ext4..."
    print_info "  Label: $LABEL"

    if ! mkfs.ext4 -L "$LABEL" "$NBD_DEVICE"; then
        print_error "Failed to format device"
        exit 1
    fi

    print_success "Formatted with ext4"
fi

print_info ""
if [ "$EXISTING_VOLUME" = true ]; then
    print_info "Step 2/3: Populating config files..."
else
    print_info "Step 4/5: Populating config files..."
fi

MOUNT_DIR="/tmp/tdx-config-mount-$$"
mkdir -p "$MOUNT_DIR"

if ! mount "$NBD_DEVICE" "$MOUNT_DIR"; then
    print_error "Failed to mount config volume"
    rmdir "$MOUNT_DIR" 2>/dev/null || true
    exit 1
fi

populate_config_into_mount "$MOUNT_DIR"

sync
umount "$MOUNT_DIR"
rmdir "$MOUNT_DIR"

if [ "$EXISTING_VOLUME" = true ]; then
    print_success "Config files written and volume unmounted"
else
    print_success "Config files created and volume unmounted"
fi

print_info ""
if [ "$EXISTING_VOLUME" = true ]; then
    print_info "Step 3/3: Verifying volume..."
else
    print_info "Step 5/5: Verifying volume..."
fi

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

qemu-nbd --disconnect "$NBD_DEVICE" &> /dev/null

print_info ""
if [ "$EXISTING_VOLUME" = true ]; then
    print_success "Config volume updated successfully!"
else
    print_success "Config volume created successfully!"
fi
print_info ""
print_info "Volume details:"
print_info "  Path: $OUTPUT_PATH"
if [ "$EXISTING_VOLUME" != true ]; then
    print_info "  Size: 10M"
fi
print_info "  Filesystem: ext4"
print_info "  Label: $LABEL"
print_info ""
print_info "Config files:"
print_info "  /hostname         : $HOSTNAME"
print_info "  /miner-ss58       : [credential file]"
print_info "  /miner-seed       : [credential file]"
print_info "  /network-config.yaml : ${VM_IP} via ${VM_GATEWAY}"
if [[ -n "$DOCKER_HUB_USER" && -n "$DOCKER_HUB_TOKEN" ]]; then
    print_info "  /docker-hub-username : [credential file]"
    print_info "  /docker-hub-token    : [credential file]"
fi
print_info ""
print_info "To use with run-td:"
print_info "  python3 ./run-td --config-volume $OUTPUT_PATH [other options...]"
print_info ""
print_info "To verify the volume contents later:"
print_info "  sudo qemu-nbd --connect=/dev/nbd0 $OUTPUT_PATH"
print_info "  sudo mkdir /mnt/verify && sudo mount /dev/nbd0 /mnt/verify"
print_info "  sudo ls -la /mnt/verify/"
print_info "  sudo umount /mnt/verify && sudo qemu-nbd --disconnect /dev/nbd0"

exit 0
