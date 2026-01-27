#!/bin/bash
# Verify storage volume encryption in production mode
# This runs BEFORE any data copying to ensure we don't copy sensitive data to unencrypted volumes
# This runs AFTER the device has been unlocked and mounted

set -euo pipefail

STORAGE_BASE="/cache/storage"
DEBUG_MODE="${DEBUG_MODE:-false}"
LOG_TAG="verify-storage"

log_info() {
    echo "[$LOG_TAG] $*" | systemd-cat -t "$LOG_TAG" -p info
    echo "[$LOG_TAG] $*"
}

log_error() {
    echo "[$LOG_TAG] ERROR: $*" | systemd-cat -t "$LOG_TAG" -p err
    echo "[$LOG_TAG] ERROR: $*" >&2
}

# Ensure storage volume is mounted
if ! mountpoint -q "$STORAGE_BASE"; then
    log_error "$STORAGE_BASE is not mounted"
    exit 1
fi

# Verify encryption in production mode
if [ "$DEBUG_MODE" != "true" ]; then
    # Production mode: verify the mounted device is a LUKS mapper device
    MOUNT_SOURCE=$(findmnt -n -o SOURCE "$STORAGE_BASE" 2>/dev/null || echo "")
    
    if [ -z "$MOUNT_SOURCE" ]; then
        log_error "Failed to determine mount source for $STORAGE_BASE"
        exit 1
    fi
    
    # Check if it's a mapper device (should be /dev/mapper/storage in production)
    if [[ "$MOUNT_SOURCE" != /dev/mapper/* ]]; then
        log_error "Storage volume is not mounted from a LUKS mapper device in production mode"
        log_error "Expected /dev/mapper/storage, got: $MOUNT_SOURCE"
        exit 1
    fi
    
    # Verify it's actually a dm-crypt device
    MAPPER_NAME=$(basename "$MOUNT_SOURCE")
    if [ ! -e "/dev/mapper/$MAPPER_NAME" ]; then
        log_error "Mapper device /dev/mapper/$MAPPER_NAME does not exist"
        exit 1
    fi
    
    # Check if it's a crypt device using dmsetup
    # dm-crypt devices will have "crypt" in their table type
    if ! dmsetup table "$MAPPER_NAME" 2>/dev/null | grep -q "crypt"; then
        log_error "Storage volume does not appear to be encrypted"
        log_error "Device $MOUNT_SOURCE is not a dm-crypt device"
        log_error "In production mode, storage must be encrypted with LUKS"
        exit 1
    fi
    
    log_info "Encryption verification passed: $MOUNT_SOURCE is a LUKS encrypted device"
else
    # Debug mode: verify it's using the unencrypted device by label
    MOUNT_SOURCE=$(findmnt -n -o SOURCE "$STORAGE_BASE" 2>/dev/null || echo "")
    
    if [ -z "$MOUNT_SOURCE" ]; then
        log_error "Failed to determine mount source for $STORAGE_BASE"
        exit 1
    fi
    
    # Check that it's NOT a mapper device (should be unencrypted)
    if [[ "$MOUNT_SOURCE" == /dev/mapper/* ]]; then
        log_error "Storage volume is mounted from a mapper device in debug mode"
        log_error "Debug mode must use an unencrypted device, got: $MOUNT_SOURCE"
        exit 1
    fi
    
    # Resolve the actual device (in case it's a symlink like /dev/disk/by-label/storage)
    ACTUAL_DEVICE=$(readlink -f "$MOUNT_SOURCE" 2>/dev/null || echo "$MOUNT_SOURCE")
    
    # Verify the device is not encrypted (not a LUKS device)
    if cryptsetup isLuks "$ACTUAL_DEVICE" 2>/dev/null; then
        log_error "Storage volume device $ACTUAL_DEVICE is encrypted in debug mode"
        log_error "Debug mode must use an unencrypted device"
        exit 1
    fi
    
    # Verify the device has the expected label
    DEVICE_LABEL=$(blkid -o value -s LABEL "$ACTUAL_DEVICE" 2>/dev/null || echo "")
    if [ "$DEVICE_LABEL" != "storage" ]; then
        log_error "Storage volume device has wrong label: '$DEVICE_LABEL' (expected 'storage')"
        log_error "Device: $ACTUAL_DEVICE"
        exit 1
    fi
    
    log_info "Debug mode verification passed: using unencrypted device $MOUNT_SOURCE (label: storage)"
fi

log_info "Storage verification complete"
exit 0
