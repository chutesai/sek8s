#!/bin/bash
# Initialize k3s storage on storage volume: create k3s dir, sync from VM root when storage is empty
# (first boot from build), or ensure subdirs exist. Same sync condition as init-kubelet-storage:
# when storage has no top-level items and root has k3s dir, sync.
# Runs AFTER storage verification and mount, BEFORE setup-storage-bind-mounts.

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

# Create k3s directory on storage (do not create subdirs yet so empty-check matches kubelet logic)
mkdir -p "$K3S_STORAGE_TARGET"

# Ensure source exists so we can sync (create if missing, e.g. image never ran k3s during build)
mkdir -p "$K3S_SOURCE"

# When storage k3s is empty, sync from VM root (first boot from build or source was just created).
file_count=$(find "$K3S_STORAGE_TARGET" -mindepth 1 -maxdepth 1 ! -name "lost+found" 2>/dev/null | wc -l)
log_info "K3s storage check: file_count=$file_count ($K3S_SOURCE -> $K3S_STORAGE_TARGET)"
if [[ "$file_count" -eq 0 ]]; then
    log_info "K3s on storage is empty, syncing from VM root"
    if rsync -a --exclude='lost+found' "$K3S_SOURCE/" "$K3S_STORAGE_TARGET/"; then
        log_info "K3s synced successfully ($(du -sh "$K3S_STORAGE_TARGET" 2>/dev/null | cut -f1))"
    else
        log_error "Failed to sync k3s to storage"
        exit 1
    fi
else
    log_info "K3s storage already initialized ($file_count top-level items)"
fi

# Ensure required subdirs exist (for fresh volume or if rsync did not create them)
mkdir -p "$K3S_STORAGE_TARGET/init-markers"
mkdir -p "$K3S_STORAGE_TARGET/credentials"

log_info "K3s storage initialization complete"
exit 0
