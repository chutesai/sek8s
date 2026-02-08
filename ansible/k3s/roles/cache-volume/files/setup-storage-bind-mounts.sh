#!/bin/bash
# setup-storage-bind-mounts.sh - Set up bind mounts: k3s, full kubelet, Chutes agent, admission certs on storage volume
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
K3S_SOURCE="${STORAGE_BASE}/k3s"
K3S_TARGET="/var/lib/rancher/k3s"
ADMISSION_CERTS_SOURCE="${STORAGE_BASE}/admission-controller-certs"
ADMISSION_CERTS_TARGET="/etc/admission-controller/certs"
KUBELET_SOURCE="${STORAGE_BASE}/kubelet"
CHUTES_AGENT_SOURCE="${STORAGE_BASE}/chutes-agent"
KUBELET_TARGET="/var/lib/kubelet"
CHUTES_AGENT_TARGET="/var/lib/chutes/agent"

# Ensure storage volume is mounted
if ! mountpoint -q "$STORAGE_BASE"; then
    log "ERROR: $STORAGE_BASE is not mounted, cannot create bind mounts"
    exit 1
fi

# Setup entire k3s directory on storage (sync from VM root is done by init-k3s-storage.sh before we run)
log "Ensuring k3s directory on storage volume..."
mkdir -p "$K3S_SOURCE"
mkdir -p "$K3S_SOURCE/init-markers"
mkdir -p "$K3S_SOURCE/credentials"
mkdir -p "$K3S_TARGET"

if mountpoint -q "$K3S_TARGET"; then
    log "K3s target already mounted, checking if it's the correct bind mount..."
    if [ "$(stat -c %d "$K3S_TARGET")" = "$(stat -c %d "$K3S_SOURCE")" ]; then
        log "K3s bind mount already correctly configured"
    else
        log "WARNING: K3s target is mounted but not our bind mount. Skipping."
    fi
else
    log "Creating bind mount: $K3S_SOURCE -> $K3S_TARGET"
    if mount --bind "$K3S_SOURCE" "$K3S_TARGET"; then
        log "K3s bind mount created successfully"
    else
        log "ERROR: Failed to create k3s bind mount"
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

# Setup full kubelet directory on storage (so node ephemeral-storage capacity reflects the large volume)
log "Ensuring kubelet directory on storage volume..."
mkdir -p "$KUBELET_SOURCE"
mkdir -p "$KUBELET_TARGET"

if mountpoint -q "$KUBELET_TARGET"; then
    log "Kubelet target already mounted, checking if it's the correct bind mount..."
    if [ "$(stat -c %d "$KUBELET_TARGET")" = "$(stat -c %d "$KUBELET_SOURCE")" ]; then
        log "Kubelet bind mount already correctly configured"
    else
        log "WARNING: Kubelet target is mounted but not our bind mount. Skipping."
    fi
else
    log "Creating bind mount: $KUBELET_SOURCE -> $KUBELET_TARGET"
    if mount --bind "$KUBELET_SOURCE" "$KUBELET_TARGET"; then
        log "Kubelet bind mount created successfully"
    else
        log "ERROR: Failed to create kubelet bind mount"
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
