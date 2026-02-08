#!/bin/bash
# Initialize k3s storage on storage volume by copying entire k3s dir from root when volume is empty.
# Runs AFTER storage verification and mount, BEFORE setup-storage-bind-mounts (so bind mount can overlay).

set -euo pipefail

STORAGE_BASE="/cache/storage"
K3S_SOURCE="/var/lib/rancher/k3s"
K3S_STORAGE_TARGET="${STORAGE_BASE}/k3s"
LOG_TAG="init-k3s-storage"

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

# Create k3s directory on storage (do not create subdirs yet so empty-check is accurate)
mkdir -p "$K3S_STORAGE_TARGET"

# Check if storage k3s is empty (fresh volume - no server/agent state yet)
file_count=$(find "$K3S_STORAGE_TARGET" -mindepth 1 -maxdepth 1 ! -name "lost+found" 2>/dev/null | wc -l)

if [[ "$file_count" -eq 0 ]]; then
    # Storage is empty; sync from VM root if it has existing k3s state (e.g. build with preinstalled k3s)
    if [ -d "$K3S_SOURCE" ] && { [ -d "${K3S_SOURCE}/server/db" ] || [ -f "${K3S_SOURCE}/server/token" ] || [ -d "${K3S_SOURCE}/agent" ]; }; then
        log_info "K3s on storage is empty, syncing from VM root ($K3S_SOURCE -> $K3S_STORAGE_TARGET)"
        if rsync -a --exclude='lost+found' "$K3S_SOURCE/" "$K3S_STORAGE_TARGET/"; then
            log_info "K3s state synced successfully ($(du -sh "$K3S_STORAGE_TARGET" 2>/dev/null | cut -f1))"
        else
            log_error "Failed to sync k3s state to storage"
            exit 1
        fi
    else
        log_info "VM root has no k3s state to sync; storage will start fresh"
    fi
else
    log_info "K3s storage already initialized ($file_count top-level items)"
fi

# Ensure required subdirs exist (for fresh volume or if rsync did not create them)
mkdir -p "$K3S_STORAGE_TARGET/init-markers"
mkdir -p "$K3S_STORAGE_TARGET/credentials"

log_info "K3s storage initialization complete"
exit 0
