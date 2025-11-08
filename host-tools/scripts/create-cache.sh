#!/usr/bin/env bash
# create-cache-volume.sh - Create and format a cache volume for TDX VMs
# Usage: ./create-cache-volume.sh <output-path> <size>
# Example: ./create-cache-volume.sh cache-volume.qcow2 500G

set -euo pipefail

# Max 16 chars
LABEL="tdx-cache"

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

# Check for help flag
if [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
    cat << EOF
Usage: $0 <output-path> <size>

Create and format a cache volume for TDX VMs with the required label.

Arguments:
  output-path    Path where the qcow2 file will be created
  size          Size of the volume (e.g., 500G, 1T, 100G)

Examples:
  $0 cache-volume.qcow2 500G
  $0 /path/to/my-cache.qcow2 1T
  $0 test-cache.qcow2 100G

The volume will be formatted with:
  - Filesystem: ext4
  - Label: $LABEL (required by TDX VMs)

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
if [ $# -ne 2 ]; then
    print_error "Invalid number of arguments"
    echo "Usage: $0 <output-path> <size>"
    echo "Example: $0 cache-volume.qcow2 500G"
    echo "Run '$0 --help' for more information"
    exit 1
fi

OUTPUT_PATH="$1"
SIZE="$2"

# Validate size format
if ! [[ "$SIZE" =~ ^[0-9]+[KMGT]?$ ]]; then
    print_error "Invalid size format: $SIZE"
    echo "Size must be a number followed by optional unit (K, M, G, T)"
    echo "Examples: 500G, 1T, 100000M"
    exit 1
fi

# Check if output file already exists
if [ -f "$OUTPUT_PATH" ]; then
    print_error "Output file already exists: $OUTPUT_PATH"
    echo "Please remove it first or choose a different path"
    exit 1
fi

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
    echo "Try: sudo $0 $OUTPUT_PATH $SIZE"
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

# Step 1: Create qcow2 image
print_info ""
print_info "Step 1/4: Creating qcow2 image..."
print_info "  Path: $OUTPUT_PATH"
print_info "  Size: $SIZE"

if ! qemu-img create -f qcow2 "$OUTPUT_PATH" "$SIZE"; then
    print_error "Failed to create qcow2 image"
    exit 1
fi

print_success "qcow2 image created"

# Step 2: Connect to NBD
print_info ""
print_info "Step 2/4: Connecting to NBD device..."

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
print_info "Step 3/4: Formatting with ext4..."
print_info "  Label: $LABEL"

if ! mkfs.ext4 -L "$LABEL" "$NBD_DEVICE"; then
    print_error "Failed to format device"
    exit 1
fi

print_success "Formatted with ext4"

# Step 4: Verify
print_info ""
print_info "Step 4/4: Verifying volume..."

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
print_success "Cache volume created successfully!"
print_info ""
print_info "Volume details:"
print_info "  Path: $OUTPUT_PATH"
print_info "  Size: $SIZE"
print_info "  Filesystem: ext4"
print_info "  Label: $LABEL"
print_info ""
print_info "To use with run-tdx.sh:"
print_info "  ./run-tdx.sh --cache-volume $OUTPUT_PATH [other options...]"
print_info ""
print_info "To verify the volume later:"
print_info "  sudo qemu-nbd --connect=/dev/nbd0 $OUTPUT_PATH"
print_info "  sudo blkid /dev/nbd0"
print_info "  sudo qemu-nbd --disconnect /dev/nbd0"

exit 0