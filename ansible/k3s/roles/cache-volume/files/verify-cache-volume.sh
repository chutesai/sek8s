#!/bin/bash
# verify-cache-volume.sh - Verify cache volume encryption after mounting
# Runs AFTER var-snap.mount. Production: verify /var/snap is from LUKS mapper.
# Debug: verify mount is from unencrypted device with label tdx-cache.
# If verification fails, the service fails and OnFailure=poweroff.target applies.

set -euo pipefail

SNAP_MOUNT="/var/snap"
EXPECTED_LABEL="tdx-cache"
DEBUG_MODE="${DEBUG_MODE:-false}"
LOG_TAG="verify-cache-volume"

log_info() {
    echo "[$LOG_TAG] $*" | systemd-cat -t "$LOG_TAG" -p info 2>/dev/null || true
    echo "[$LOG_TAG] $*"
}

log_error() {
    echo "[$LOG_TAG] ERROR: $*" | systemd-cat -t "$LOG_TAG" -p err 2>/dev/null || true
    echo "[$LOG_TAG] ERROR: $*" >&2
}

if ! mountpoint -q "$SNAP_MOUNT"; then
    log_error "$SNAP_MOUNT is not mounted"
    exit 1
fi

if [ "$DEBUG_MODE" != "true" ]; then
    # Production: verify the mounted device is a LUKS mapper device
    MOUNT_SOURCE=$(findmnt -n -o SOURCE "$SNAP_MOUNT" 2>/dev/null || echo "")
    if [ -z "$MOUNT_SOURCE" ]; then
        log_error "Failed to determine mount source for $SNAP_MOUNT"
        exit 1
    fi
    if [[ "$MOUNT_SOURCE" != /dev/mapper/* ]]; then
        log_error "Cache volume is not mounted from a LUKS mapper device in production mode"
        log_error "Expected /dev/mapper/tdx-cache, got: $MOUNT_SOURCE"
        exit 1
    fi
    MAPPER_NAME=$(basename "$MOUNT_SOURCE")
    if [ ! -e "/dev/mapper/$MAPPER_NAME" ]; then
        log_error "Mapper device /dev/mapper/$MAPPER_NAME does not exist"
        exit 1
    fi
    if ! dmsetup table "$MAPPER_NAME" 2>/dev/null | grep -q "crypt"; then
        log_error "Cache volume does not appear to be encrypted"
        log_error "Device $MOUNT_SOURCE is not a dm-crypt device"
        exit 1
    fi
    log_info "Encryption verification passed: $MOUNT_SOURCE is a LUKS encrypted device"
else
    # Debug: verify it's using the unencrypted device by label
    MOUNT_SOURCE=$(findmnt -n -o SOURCE "$SNAP_MOUNT" 2>/dev/null || echo "")
    if [ -z "$MOUNT_SOURCE" ]; then
        log_error "Failed to determine mount source for $SNAP_MOUNT"
        exit 1
    fi
    if [[ "$MOUNT_SOURCE" == /dev/mapper/* ]]; then
        log_error "Cache volume is mounted from a mapper device in debug mode"
        log_error "Debug mode must use an unencrypted device, got: $MOUNT_SOURCE"
        exit 1
    fi
    ACTUAL_DEVICE=$(readlink -f "$MOUNT_SOURCE" 2>/dev/null || echo "$MOUNT_SOURCE")
    if cryptsetup isLuks "$ACTUAL_DEVICE" 2>/dev/null; then
        log_error "Cache volume device $ACTUAL_DEVICE is encrypted in debug mode"
        exit 1
    fi
    DEVICE_LABEL=$(blkid -o value -s LABEL "$ACTUAL_DEVICE" 2>/dev/null || echo "")
    if [ "$DEVICE_LABEL" != "$EXPECTED_LABEL" ]; then
        log_error "Cache volume device has wrong label: '$DEVICE_LABEL' (expected '$EXPECTED_LABEL')"
        exit 1
    fi
    log_info "Debug mode verification passed: using unencrypted device $MOUNT_SOURCE (label: $EXPECTED_LABEL)"
fi

log_info "Cache volume verification complete"
exit 0
