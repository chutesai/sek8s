#!/bin/bash
# verify-cache-device.sh - Verify cache device exists before mounting
# Runs BEFORE var-snap.mount. Ensures the cache device/source is present so mount
# can succeed and we get a clear error message if the cache volume was not attached.
# Production: verify /dev/mapper/tdx-cache exists (initramfs created it only if device was found).
# Debug: verify block device with label tdx-cache exists.
# On failure the service fails and OnFailure=poweroff.target applies.

set -euo pipefail

EXPECTED_LABEL="tdx-cache"
DEBUG_MODE="${DEBUG_MODE:-false}"
LOG_TAG="verify-cache-device"

log_info() {
    echo "[$LOG_TAG] $*" | systemd-cat -t "$LOG_TAG" -p info
    echo "[$LOG_TAG] $*"
}

log_error() {
    echo "[$LOG_TAG] ERROR: $*" | systemd-cat -t "$LOG_TAG" -p err
    echo "[$LOG_TAG] ERROR: $*" >&2
}

if [ "$DEBUG_MODE" != "true" ]; then
    # Production: mapper device must exist (initramfs created it only when cache device was found)
    MAPPER_DEVICE="/dev/mapper/tdx-cache"
    if [ ! -b "$MAPPER_DEVICE" ]; then
        log_error "Cache mapper device not found: $MAPPER_DEVICE"
        log_error "The cache volume was not set up in initramfs (device missing or initramfs failed)"
        log_error "Shutting down - cache is required for this VM"
        sync
        shutdown -h now
        exit 1
    fi
    log_info "Cache mapper device found: $MAPPER_DEVICE"
else
    # Debug: block device with label tdx-cache must exist
    DEVICE=$(blkid -l -o device -t LABEL="$EXPECTED_LABEL" 2>/dev/null)
    if [ -z "$DEVICE" ]; then
        log_error "Cache device with label '$EXPECTED_LABEL' not found"
        log_error "Available block devices:"
        blkid 2>/dev/null | while read -r line; do
            log_error "  $line"
        done
        log_error "The cache volume was not attached to this VM or has wrong label"
        log_error "Shutting down - cache is required for this VM"
        sync
        shutdown -h now
        exit 1
    fi
    if [ ! -b "$DEVICE" ]; then
        log_error "Cache device $DEVICE is not a block device"
        sync
        shutdown -h now
        exit 1
    fi
    log_info "Cache device found: $DEVICE (label: $EXPECTED_LABEL)"
fi

log_info "Cache device verification complete"
exit 0
