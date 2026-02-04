#!/bin/bash
# setup-storage-bind-mounts.sh - Set up bind mounts for k3s server, containerd, kubelet-pods, and Chutes agent on storage volume
set -euo pipefail

LOG_TAG="setup-storage-bind-mounts"

log() {
    echo "$1"
    logger -t "$LOG_TAG" "$1" 2>/dev/null || true
}

# UID:GID for pod/container access (must match runAsUser/runAsGroup in pod spec)
STORAGE_OWNER="1000:1000"

# Storage volume mount point (same for both production and debug VMs)
STORAGE_BASE="/cache/storage"
K3S_SERVER_SOURCE="${STORAGE_BASE}/k3s-server"
K3S_SERVER_TARGET="/var/lib/rancher/k3s/server"
ADMISSION_CERTS_SOURCE="${STORAGE_BASE}/admission-controller-certs"
ADMISSION_CERTS_TARGET="/etc/admission-controller/certs"
CONTAINERD_SOURCE="${STORAGE_BASE}/containerd"
KUBELET_PODS_SOURCE="${STORAGE_BASE}/kubelet-pods"
CHUTES_AGENT_SOURCE="${STORAGE_BASE}/chutes-agent"
CONTAINERD_TARGET="/var/lib/rancher/k3s/agent/containerd"
KUBELET_PODS_TARGET="/var/lib/kubelet/pods"
CHUTES_AGENT_TARGET="/var/lib/chutes/agent"

# Ensure storage volume is mounted
if ! mountpoint -q "$STORAGE_BASE"; then
    log "ERROR: $STORAGE_BASE is not mounted, cannot create bind mounts"
    exit 1
fi

# Setup k3s server directory on storage (cluster state persists across VM upgrades)
log "Ensuring k3s server directory on storage volume..."
mkdir -p "$K3S_SERVER_SOURCE"
mkdir -p "$K3S_SERVER_TARGET"

# One-time sync: if storage has no server state but VM has (e.g. build with preinstalled charts), copy over
k3s_server_file_count=$(find "$K3S_SERVER_SOURCE" -mindepth 1 -maxdepth 1 ! -name "lost+found" 2>/dev/null | wc -l)
if [[ "$k3s_server_file_count" -eq 0 ]]; then
    if [ -d "$K3S_SERVER_TARGET" ] && { [ -d "${K3S_SERVER_TARGET}/db" ] || [ -f "${K3S_SERVER_TARGET}/token" ]; }; then
        log "K3s server on storage is empty, syncing initial state from build VM..."
        if rsync -a --exclude='lost+found' "$K3S_SERVER_TARGET/" "$K3S_SERVER_SOURCE/"; then
            log "K3s server state synced successfully ($(du -sh "$K3S_SERVER_SOURCE" 2>/dev/null | cut -f1))"
        else
            log "ERROR: Failed to sync k3s server state to storage"
            exit 1
        fi
    fi
fi

if mountpoint -q "$K3S_SERVER_TARGET"; then
    log "K3s server target already mounted, checking if it's the correct bind mount..."
    if [ "$(stat -c %d "$K3S_SERVER_TARGET")" = "$(stat -c %d "$K3S_SERVER_SOURCE")" ]; then
        log "K3s server bind mount already correctly configured"
    else
        log "WARNING: K3s server target is mounted but not our bind mount. Skipping."
    fi
else
    log "Creating bind mount: $K3S_SERVER_SOURCE -> $K3S_SERVER_TARGET"
    if mount --bind "$K3S_SERVER_SOURCE" "$K3S_SERVER_TARGET"; then
        log "K3s server bind mount created successfully"
    else
        log "ERROR: Failed to create k3s server bind mount"
        exit 1
    fi
fi

# Setup admission controller certs on storage (must match caBundle in cluster webhook config across VM replacements)
log "Ensuring admission controller certs on storage volume..."
mkdir -p "$ADMISSION_CERTS_SOURCE"
mkdir -p "$ADMISSION_CERTS_TARGET"
admission_certs_file_count=$(find "$ADMISSION_CERTS_SOURCE" -mindepth 1 -maxdepth 1 ! -name "lost+found" 2>/dev/null | wc -l)
if [[ "$admission_certs_file_count" -eq 0 ]]; then
    if [ -d "$ADMISSION_CERTS_TARGET" ] && [ -f "${ADMISSION_CERTS_TARGET}/server.crt" ]; then
        log "Admission controller certs on storage are empty, syncing from build VM..."
        if rsync -a --exclude='lost+found' "$ADMISSION_CERTS_TARGET/" "$ADMISSION_CERTS_SOURCE/"; then
            log "Admission controller certs synced successfully"
        else
            log "ERROR: Failed to sync admission controller certs to storage"
            exit 1
        fi
    fi
fi
if mountpoint -q "$ADMISSION_CERTS_TARGET"; then
    log "Admission controller certs target already mounted, checking bind mount..."
    if [ "$(stat -c %d "$ADMISSION_CERTS_TARGET")" = "$(stat -c %d "$ADMISSION_CERTS_SOURCE")" ]; then
        log "Admission controller certs bind mount already correctly configured"
    else
        log "WARNING: Admission controller certs target is mounted but not our bind mount. Skipping."
    fi
else
    log "Creating bind mount: $ADMISSION_CERTS_SOURCE -> $ADMISSION_CERTS_TARGET"
    if mount --bind "$ADMISSION_CERTS_SOURCE" "$ADMISSION_CERTS_TARGET"; then
        log "Admission controller certs bind mount created successfully"
    else
        log "ERROR: Failed to create admission controller certs bind mount"
        exit 1
    fi
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

# Chutes agent on storage: create dir with pod-friendly permissions, then bind mount
mkdir -p "$CHUTES_AGENT_SOURCE"
chown -R "$STORAGE_OWNER" "$CHUTES_AGENT_SOURCE"
chmod -R 755 "$CHUTES_AGENT_SOURCE"
mkdir -p /var/lib/chutes
mkdir -p "$CHUTES_AGENT_TARGET"
if mountpoint -q "$CHUTES_AGENT_TARGET"; then
    if [ "$(stat -c %d "$CHUTES_AGENT_TARGET")" = "$(stat -c %d "$CHUTES_AGENT_SOURCE")" ]; then
        log "Chutes agent bind mount already correctly configured"
    else
        log "WARNING: Chutes agent target is mounted but not our bind mount. Skipping."
    fi
else
    log "Creating bind mount: $CHUTES_AGENT_SOURCE -> $CHUTES_AGENT_TARGET"
    if mount --bind "$CHUTES_AGENT_SOURCE" "$CHUTES_AGENT_TARGET"; then
        log "Chutes agent bind mount created successfully"
    else
        log "ERROR: Failed to create Chutes agent bind mount"
        exit 1
    fi
fi

log "Bind mounts setup complete"
exit 0
