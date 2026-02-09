#!/bin/bash
# Initialize full kubelet directory on storage volume so the kubelet root is on the large volume.
# Kubelet reports node ephemeral-storage capacity from the filesystem containing its root dir;
# mounting only kubelet/pods left the root on the small root fs. Mounting the full kubelet fixes that.
# When storage kubelet is empty, sync from VM root so we get seccomp profiles and any build-time content.
# Runs AFTER storage verification and mount, BEFORE setup-storage-bind-mounts.

set -euo pipefail

STORAGE_BASE="/cache/storage"
KUBELET_SOURCE="/var/lib/kubelet"
KUBELET_STORAGE_TARGET="${STORAGE_BASE}/kubelet"
LOG_TAG="init-kubelet-storage"

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

# Create kubelet directory on storage
mkdir -p "$KUBELET_STORAGE_TARGET"

# Ensure source exists so we can sync (create if missing, e.g. image never ran kubelet during build)
mkdir -p "$KUBELET_SOURCE"

# When storage kubelet is empty, sync from VM root (seccomp profiles, etc.).
file_count=$(find "$KUBELET_STORAGE_TARGET" -mindepth 1 -maxdepth 1 ! -name "lost+found" 2>/dev/null | wc -l)
log_info "Kubelet storage check: file_count=$file_count ($KUBELET_SOURCE -> $KUBELET_STORAGE_TARGET)"
if [[ "$file_count" -eq 0 ]]; then
    log_info "Kubelet on storage is empty, syncing from VM root"
    if rsync -a --exclude='lost+found' "$KUBELET_SOURCE/" "$KUBELET_STORAGE_TARGET/"; then
        log_info "Kubelet synced successfully ($(du -sh "$KUBELET_STORAGE_TARGET" 2>/dev/null | cut -f1))"
    else
        log_error "Failed to sync kubelet to storage"
        exit 1
    fi
else
    log_info "Kubelet storage already initialized ($file_count top-level items)"
fi

log_info "Kubelet storage initialization complete"
exit 0
