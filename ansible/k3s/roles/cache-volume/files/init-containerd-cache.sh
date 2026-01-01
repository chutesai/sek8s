#!/bin/bash
# Initialize containerd cache volume by copying existing content from root filesystem
# This runs AFTER the device has been encrypted/unlocked by the boot script (production)
# or directly on unencrypted device (debug mode)
# and BEFORE the mount unit mounts it to the final location

set -euo pipefail

CONTAINERD_SOURCE="/var/lib/rancher/k3s/agent/containerd"
TEMP_MOUNT="/mnt/containerd-init"
DEBUG_MODE="${DEBUG_MODE:-false}"
LOG_TAG="containerd-cache-init"

# Determine device based on debug mode
if [ "$DEBUG_MODE" = "true" ]; then
    # Debug mode: use unencrypted device directly (detect by label)
    DEVICE=$(blkid -l -o device -t LABEL="containerd-cache" 2>/dev/null)
    if [ -z "$DEVICE" ]; then
        echo "[$LOG_TAG] ERROR: Could not find device with label 'containerd-cache'" >&2
        exit 1
    fi
else
    # Production mode: use encrypted mapper device
    DEVICE="/dev/mapper/containerd_cache"
fi

log_info() {
    echo "[$LOG_TAG] $*" | systemd-cat -t "$LOG_TAG" -p info
    echo "[$LOG_TAG] $*"
}

log_error() {
    echo "[$LOG_TAG] ERROR: $*" | systemd-cat -t "$LOG_TAG" -p err
    echo "[$LOG_TAG] ERROR: $*" >&2
}

# Verify the device exists
if [ ! -b "$DEVICE" ]; then
    log_error "Device $DEVICE does not exist"
    if [ "$DEBUG_MODE" = "false" ]; then
        log_error "Boot script may have failed to unlock encrypted device"
    fi
    exit 1
fi

# In production mode, verify this is actually a decrypted LUKS device (security check)
if [ "$DEBUG_MODE" = "false" ]; then
    if ! dmsetup info "$DEVICE" &>/dev/null; then
        log_error "$DEVICE is not a valid device mapper device"
        exit 1
    fi
    log_info "Verified encrypted containerd cache device exists"
else
    # In debug mode, check if someone accidentally attached an encrypted volume
    if cryptsetup isLuks "$DEVICE" 2>/dev/null; then
        log_error "Device $DEVICE is LUKS encrypted but debug mode is enabled"
        log_error "Debug VMs require unencrypted containerd cache volumes"
        log_error "Either:"
        log_error "  1. Create a new unencrypted cache volume for debug VMs"
        log_error "  2. Build with debug_build: false for production VMs with encryption"
        exit 1
    fi
    log_info "Debug mode: using unencrypted containerd cache device $DEVICE"
fi

# Create temporary mount point
mkdir -p "$TEMP_MOUNT"

# Mount the containerd cache temporarily
if ! mount "$DEVICE" "$TEMP_MOUNT"; then
    log_error "Failed to mount $DEVICE to $TEMP_MOUNT"
    rmdir "$TEMP_MOUNT" 2>/dev/null || true
    exit 1
fi

# Check if the cache is empty (fresh format)
# Count files/dirs excluding lost+found (created by ext4 format)
file_count=$(find "$TEMP_MOUNT" -mindepth 1 -maxdepth 1 ! -name "lost+found" 2>/dev/null | wc -l)

if [ "$file_count" -eq 0 ]; then
    log_info "Containerd cache is empty, syncing from root filesystem"
    
    # Check if source directory exists and has content
    if [ -d "$CONTAINERD_SOURCE" ] && [ "$(ls -A "$CONTAINERD_SOURCE" 2>/dev/null)" ]; then
        log_info "Copying existing containerd data from $CONTAINERD_SOURCE"
        
        # Rsync the existing containerd data
        if rsync -a --exclude='lost+found' "$CONTAINERD_SOURCE/" "$TEMP_MOUNT/"; then
            log_info "Successfully synced containerd data ($(du -sh "$TEMP_MOUNT" | cut -f1))"
        else
            log_error "Failed to sync containerd data"
            umount "$TEMP_MOUNT"
            rmdir "$TEMP_MOUNT" 2>/dev/null || true
            exit 1
        fi
    else
        log_info "Source directory empty or missing, cache will start fresh"
    fi
else
    log_info "Containerd cache already initialized ($file_count items found)"
fi

# Unmount the temporary mount
if ! umount "$TEMP_MOUNT"; then
    log_error "Failed to unmount $TEMP_MOUNT"
    exit 1
fi

# Clean up temp mount point
rmdir "$TEMP_MOUNT" 2>/dev/null || true

log_info "Containerd cache initialization complete"
exit 0
