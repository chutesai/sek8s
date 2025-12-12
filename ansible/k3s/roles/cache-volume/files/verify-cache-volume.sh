#!/bin/bash
# verify-cache-volume.sh - Verify and prepare cache volume for mounting
# This script runs early in boot to verify the cache volume has the correct
# filesystem and label before allowing the system to mount it.
# If verification fails, the system will shut down immediately.

set -euo pipefail

EXPECTED_LABEL="tdx-cache"
LOG_TAG="verify-cache-volume"

# Function to log messages to both console and journal
log_info() {
    echo "$1"
    logger -t "$LOG_TAG" -p user.info "$1"
}

log_error() {
    echo "ERROR: $1" >&2
    logger -t "$LOG_TAG" -p user.err "ERROR: $1"
}

log_info "=== Cache Volume Verification Started ==="
log_info "Expected label: $EXPECTED_LABEL"

# Find device by label instead of hardcoding /dev/vdb
# This handles cases where cloud-init or other drives shift the device name
DEVICE=$(blkid -l -o device -t LABEL="$EXPECTED_LABEL" 2>/dev/null)

if [ -z "$DEVICE" ]; then
    log_error "Cache device with label '$EXPECTED_LABEL' not found"
    log_error "Available block devices:"
    blkid | while read line; do
        log_error "  $line"
    done
    log_error "The cache volume was not attached to this VM or has wrong label"
    log_error "Shutting down immediately to prevent boot with missing cache"
    sync
    shutdown -h now
    exit 1
fi

log_info "Cache device found: $DEVICE"

# Verify it's actually a block device
if [ ! -b "$DEVICE" ]; then
    log_error "Device $DEVICE is not a block device"
    log_error "Shutting down immediately"
    sync
    shutdown -h now
    exit 1
fi

# Give the device a moment to settle (sometimes needed after virtio attachment)
sleep 1

# Check filesystem type and label using blkid
if ! FS_INFO=$(blkid -o export "$DEVICE" 2>&1); then
    log_error "Failed to read filesystem information from $DEVICE"
    log_error "Output: $FS_INFO"
    log_error "The device may not be formatted or is corrupt"
    log_error "Shutting down immediately"
    sync
    shutdown -h now
    exit 1
fi

# Extract filesystem type and label
FS_TYPE=$(echo "$FS_INFO" | grep '^TYPE=' | cut -d= -f2 || echo "unknown")
FS_LABEL=$(echo "$FS_INFO" | grep '^LABEL=' | cut -d= -f2 || echo "none")
FS_UUID=$(echo "$FS_INFO" | grep '^UUID=' | cut -d= -f2 || echo "none")

log_info "Filesystem type: $FS_TYPE"
log_info "Filesystem label: $FS_LABEL"
log_info "Filesystem UUID: $FS_UUID"

# Verify filesystem type
if [ "$FS_TYPE" != "ext4" ]; then
    log_error "Cache device has wrong filesystem type: $FS_TYPE (expected ext4)"
    log_error "The cache volume must be formatted with ext4"
    log_error "Shutting down immediately"
    sync
    shutdown -h now
    exit 1
fi

log_info "Filesystem type verification: PASSED"

# Verify label
if [ "$FS_LABEL" != "$EXPECTED_LABEL" ]; then
    log_error "Cache device has wrong label: '$FS_LABEL' (expected '$EXPECTED_LABEL')"
    log_error "The cache volume must have the label '$EXPECTED_LABEL'"
    log_error "Shutting down immediately"
    sync
    shutdown -h now
    exit 1
fi

log_info "Filesystem label verification: PASSED"

# Optional: Run filesystem check (fsck) if needed
# This is conservative - only check if the filesystem needs it
if tune2fs -l "$DEVICE" 2>/dev/null | grep -q "needs checking"; then
    log_info "Filesystem needs checking, running fsck..."
    if ! e2fsck -p "$DEVICE"; then
        log_error "Filesystem check failed on $DEVICE"
        log_error "The cache volume may be corrupt"
        log_error "Shutting down immediately"
        sync
        shutdown -h now
        exit 1
    fi
    log_info "Filesystem check completed successfully"
fi

log_info "=== Cache Volume Verification Complete ==="
log_info "Device $DEVICE is ready to be mounted"
log_info "Filesystem: $FS_TYPE, Label: $FS_LABEL, UUID: $FS_UUID"

# === One-time cleanup of legacy containerd data ===
# Previously the entire containerd directory was cached, which could leak
# sensitive data and cause database mismatches. Now we only cache blobs.
# This cleanup removes the old data structure if present.
# TODO: Remove this cleanup block after all cache volumes have been migrated.

MOUNT_POINT="/var/snap"

# Temporarily mount to check for and clean up legacy data
log_info "Temporarily mounting $DEVICE to check for legacy data..."
mount -t ext4 -o defaults,noatime "$DEVICE" "$MOUNT_POINT"

LEGACY_CONTAINERD="$MOUNT_POINT/containerd"
CLEANUP_MARKER="$MOUNT_POINT/.containerd-cleanup-done"

if [ -d "$LEGACY_CONTAINERD" ] && [ ! -f "$CLEANUP_MARKER" ]; then
    log_info "Found legacy containerd directory, performing one-time cleanup..."

    # Preserve blobs if they exist (content-addressed, safe to keep)
    LEGACY_BLOBS="$LEGACY_CONTAINERD/io.containerd.content.v1.content/blobs"
    BLOBS_DIR="$MOUNT_POINT/containerd-blobs"

    if [ -d "$LEGACY_BLOBS" ]; then
        log_info "Migrating blobs from legacy location..."
        mkdir -p "$BLOBS_DIR"
        if [ -d "$LEGACY_BLOBS/sha256" ]; then
            mv "$LEGACY_BLOBS/sha256" "$BLOBS_DIR/" 2>/dev/null || true
        fi
    fi

    # Remove the legacy containerd directory
    log_info "Removing legacy containerd data (database, snapshots, runtime state)..."
    rm -rf "$LEGACY_CONTAINERD"

    touch "$CLEANUP_MARKER"
    log_info "Legacy containerd cleanup complete"
fi

# Ensure the blobs directory exists for the bind mount
mkdir -p "$MOUNT_POINT/containerd-blobs/sha256"

umount "$MOUNT_POINT"
log_info "Temporary mount removed, systemd will mount properly"

exit 0