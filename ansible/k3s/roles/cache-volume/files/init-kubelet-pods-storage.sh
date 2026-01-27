#!/bin/bash
# Initialize kubelet-pods storage on storage volume by copying existing content from root filesystem
# This runs AFTER storage verification and mount
# and BEFORE bind mounts are set up

set -euo pipefail

STORAGE_BASE="/cache/storage"
KUBELET_PODS_SOURCE="/var/lib/kubelet/pods"
KUBELET_PODS_CACHE_TARGET="${STORAGE_BASE}/kubelet-pods"
LOG_TAG="init-kubelet-pods-storage"

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

# Create kubelet-pods subdirectory on storage volume
mkdir -p "$KUBELET_PODS_CACHE_TARGET"

# Check if the kubelet-pods cache is empty (fresh format)
# Count files/dirs in kubelet-pods subdirectory excluding lost+found
file_count=$(find "$KUBELET_PODS_CACHE_TARGET" -mindepth 1 -maxdepth 1 ! -name "lost+found" 2>/dev/null | wc -l)

if [[ "$file_count" -eq 0 ]]; then
    log_info "Kubelet pods cache is empty, syncing from root filesystem"
    
    # Check if source directory exists and has content
    if [ -d "$KUBELET_PODS_SOURCE" ] && [ "$(ls -A "$KUBELET_PODS_SOURCE" 2>/dev/null)" ]; then
        log_info "Copying existing kubelet pods data from $KUBELET_PODS_SOURCE"
        
        # Rsync the existing kubelet pods data
        if rsync -a --exclude='lost+found' "$KUBELET_PODS_SOURCE/" "$KUBELET_PODS_CACHE_TARGET/"; then
            log_info "Successfully synced kubelet pods data ($(du -sh "$KUBELET_PODS_CACHE_TARGET" | cut -f1))"
        else
            log_error "Failed to sync kubelet pods data"
            exit 1
        fi
    else
        log_info "Source directory empty or missing, cache will start fresh"
    fi
else
    log_info "Kubelet pods cache already initialized ($file_count items found)"
fi

log_info "Kubelet pods storage initialization complete"
exit 0
