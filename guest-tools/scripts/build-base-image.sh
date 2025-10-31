#!/bin/bash
set -e

# Resolve the absolute path of the script's directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Configuration
TDX_REPO="$REPO_ROOT/tdx"
UBUNTU_VERSION="24.04"
LOGFILE="$REPO_ROOT/tdx-base-image-build.log"
CREATE_TD_SCRIPT="$TDX_REPO/guest-tools/image/create-td-image.sh"
GUEST_IMG_PATH="$TDX_REPO/guest-tools/image/tdx-guest-ubuntu-$UBUNTU_VERSION-generic.qcow2"
BUILD_IMG_PATH="$REPO_ROOT/guest-tools/image/tdx-guest-ubuntu-$UBUNTU_VERSION.qcow2"

# Clear existing logfile
echo "" > "$LOGFILE"

# Ensure prerequisites
echo "Installing dependencies..." | tee -a "$LOGFILE"
sudo apt update >> "$LOGFILE" 2>&1
sudo apt install -y git libguestfs-tools virtinst genisoimage libvirt-daemon-system >> "$LOGFILE" 2>&1

# Fix libvirt default network
echo "Ensuring libvirt default network is active..." | tee -a "$LOGFILE"
sudo virsh net-start default >> "$LOGFILE" 2>&1 || true
sudo virsh net-autostart default >> "$LOGFILE" 2>&1 || true

# Clone or update canonical/tdx
if [ ! -d "$TDX_REPO" ]; then
    echo "Initializing canonical/tdx submodule..." | tee -a "$LOGFILE"
    git -C "$REPO_ROOT" submodule update --init --recursive >> "$LOGFILE" 2>&1
fi
cd "$TDX_REPO"
echo "Checking out main branch for canonical/tdx..." | tee -a "$LOGFILE"
git checkout main >> "$LOGFILE" 2>&1
cd "$REPO_ROOT"

# Run create-td-image.sh to generate base image
echo "Building base TDX guest image..." | tee -a "$LOGFILE"
cd "$TDX_REPO/guest-tools/image"
sudo ./create-td-image.sh -v "$UBUNTU_VERSION" >> "$LOGFILE" 2>&1
cd "$REPO_ROOT"
if [ ! -f "$GUEST_IMG_PATH" ]; then
    echo "Error: Failed to create base TDX guest image at $GUEST_IMG_PATH" | tee -a "$LOGFILE"
    exit 1
fi

echo "Copying guest image to use for build process" | tee -a "$LOGFILE"
cp $GUEST_IMG_PATH $BUILD_IMG_PATH

# Output result
echo "Base TDX guest image created: $GUEST_IMG_PATH" | tee -a "$LOGFILE"
echo "Build TDX image created: $BUILD_IMG_PATH" | tee -a "$LOGFILE"
