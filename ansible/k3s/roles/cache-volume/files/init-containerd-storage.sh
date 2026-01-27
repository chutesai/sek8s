#!/bin/bash
# Initialize containerd storage on storage volume by copying existing content from root filesystem
# This runs AFTER storage verification and mount
# and BEFORE bind mounts are set up

set -euo pipefail

STORAGE_BASE="/cache/storage"
CONTAINERD_SOURCE="/var/lib/rancher/k3s/agent/containerd"
CONTAINERD_CACHE_TARGET="${STORAGE_BASE}/containerd"
LOG_TAG="init-containerd-storage"

log_info() {
    echo "[$LOG_TAG] $*" | systemd-cat -t "$LOG_TAG" -p info
    echo "[$LOG_TAG] $*"
}

log_error() {
    echo "[$LOG_TAG] ERROR: $*" | systemd-cat -t "$LOG_TAG" -p err
    echo "[$LOG_TAG] ERROR: $*" >&2
}

# Ensure storage volume is mounted (verification already done by verify-storage.service)
if ! mountpoint -q "$STORAGE_BASE"; then
    log_error "$STORAGE_BASE is not mounted"
    exit 1
fi

# Create containerd subdirectory on storage volume
mkdir -p "$CONTAINERD_CACHE_TARGET"

# Check if the containerd cache is empty (fresh format)
# Count files/dirs in containerd subdirectory excluding lost+found
file_count=$(find "$CONTAINERD_CACHE_TARGET" -mindepth 1 -maxdepth 1 ! -name "lost+found" 2>/dev/null | wc -l)

if [[ "$file_count" -eq 0 ]]; then
    log_info "Containerd cache is empty, syncing from root filesystem"
    
    # Check if source directory exists and has content
    if [ -d "$CONTAINERD_SOURCE" ] && [ "$(ls -A "$CONTAINERD_SOURCE" 2>/dev/null)" ]; then
        log_info "Copying existing containerd data from $CONTAINERD_SOURCE"
        
        # Rsync the existing containerd data
        if rsync -a --exclude='lost+found' "$CONTAINERD_SOURCE/" "$CONTAINERD_CACHE_TARGET/"; then
            log_info "Successfully synced containerd data ($(du -sh "$CONTAINERD_CACHE_TARGET" | cut -f1))"
        else
            log_error "Failed to sync containerd data"
            exit 1
        fi
    else
        log_info "Source directory empty or missing, cache will start fresh"
    fi
else
    log_info "Containerd cache already initialized ($file_count items found)"
fi

log_info "Containerd storage initialization complete"
exit 0
