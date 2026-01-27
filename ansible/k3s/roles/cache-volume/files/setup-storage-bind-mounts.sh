#!/bin/bash
# setup-storage-bind-mounts.sh - Set up bind mounts for containerd and kubelet-pods on storage volume
set -euo pipefail

LOG_TAG="setup-storage-bind-mounts"

log() {
    echo "$1"
    logger -t "$LOG_TAG" "$1" 2>/dev/null || true
}

# Storage volume mount point (same for both production and debug VMs)
STORAGE_BASE="/cache/storage"
CONTAINERD_SOURCE="${STORAGE_BASE}/containerd"
KUBELET_PODS_SOURCE="${STORAGE_BASE}/kubelet-pods"
CONTAINERD_TARGET="/var/lib/rancher/k3s/agent/containerd"
KUBELET_PODS_TARGET="/var/lib/kubelet/pods"

# Ensure storage volume is mounted
if ! mountpoint -q "$STORAGE_BASE"; then
    log "ERROR: $STORAGE_BASE is not mounted, cannot create bind mounts"
    exit 1
fi

# Create subdirectories on storage volume (init script handles containerd, we just ensure kubelet-pods exists)
log "Ensuring subdirectories exist on storage volume..."
mkdir -p "$CONTAINERD_SOURCE"
mkdir -p "$KUBELET_PODS_SOURCE"

# Ensure parent directories exist
mkdir -p "$(dirname "$CONTAINERD_TARGET")"
mkdir -p "$(dirname "$KUBELET_PODS_TARGET")"

# Setup containerd bind mount
if mountpoint -q "$CONTAINERD_TARGET"; then
    log "Containerd target already mounted, checking if it's the correct bind mount..."
    # Check if it's already our bind mount by comparing device
    if [ "$(stat -c %d "$CONTAINERD_TARGET")" = "$(stat -c %d "$CONTAINERD_SOURCE")" ]; then
        log "Containerd bind mount already correctly configured"
    else
        log "WARNING: Containerd target is mounted but not our bind mount. Skipping."
    fi
else
    # Create bind mount (init script already handled data migration)
    log "Creating bind mount: $CONTAINERD_SOURCE -> $CONTAINERD_TARGET"
    if mount --bind "$CONTAINERD_SOURCE" "$CONTAINERD_TARGET"; then
        log "Containerd bind mount created successfully"
    else
        log "ERROR: Failed to create containerd bind mount"
        exit 1
    fi
fi

# Setup kubelet-pods bind mount
if mountpoint -q "$KUBELET_PODS_TARGET"; then
    log "Kubelet pods target already mounted, checking if it's the correct bind mount..."
    if [ "$(stat -c %d "$KUBELET_PODS_TARGET")" = "$(stat -c %d "$KUBELET_PODS_SOURCE")" ]; then
        log "Kubelet pods bind mount already correctly configured"
    else
        log "WARNING: Kubelet pods target is mounted but not our bind mount. Skipping."
    fi
else
    # Create bind mount (no migration needed - kubelet-pods starts empty)
    log "Creating bind mount: $KUBELET_PODS_SOURCE -> $KUBELET_PODS_TARGET"
    if mount --bind "$KUBELET_PODS_SOURCE" "$KUBELET_PODS_TARGET"; then
        log "Kubelet pods bind mount created successfully"
    else
        log "ERROR: Failed to create kubelet pods bind mount"
        exit 1
    fi
fi

log "Bind mounts setup complete"
exit 0
