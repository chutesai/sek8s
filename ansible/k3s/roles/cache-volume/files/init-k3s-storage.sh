#!/bin/bash
# Initialize k3s storage on storage volume: create k3s dir and required subdirs on empty volume.
# We do NOT sync from VM root so that a recreated (empty) volume gives a fresh cluster and
# a new miner kubeconfig/certificate at first boot (03-k3s-miner-kubeconfig.sh).
# Runs AFTER storage verification and mount, BEFORE setup-storage-bind-mounts.

set -euo pipefail

STORAGE_BASE="/cache/storage"
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

# Create k3s directory and required subdirs; k3s and cluster-init will populate on first boot
mkdir -p "$K3S_STORAGE_TARGET"

# Optional: force a fresh cluster (new CA, new miner kubeconfig) when the volume was recreated.
# If this marker exists on the storage volume, we remove existing server/, agent/, and init-markers
# so k3s generates a new CA and cluster-init scripts (e.g. miner-kubeconfig) run again.
# Create it when attaching a new/empty volume (e.g. touch /cache/storage/.k3s-fresh from host or
# cloud-init), or leave absent to keep existing cluster state.
if [[ -f "${STORAGE_BASE}/.k3s-fresh" ]]; then
    for d in server agent; do
        if [[ -d "${K3S_STORAGE_TARGET}/${d}" ]]; then
            log_info "Removing existing ${d}/ (/.k3s-fresh present) so k3s starts with fresh cluster and new CA"
            rm -rf "${K3S_STORAGE_TARGET:?}/${d}"
        fi
    done
    if [[ -d "${K3S_STORAGE_TARGET}/init-markers" ]]; then
        log_info "Clearing init-markers so cluster-init scripts run again"
        rm -rf "${K3S_STORAGE_TARGET}/init-markers"
    fi
    rm -f "${STORAGE_BASE}/.k3s-fresh"
fi

mkdir -p "$K3S_STORAGE_TARGET/init-markers"
mkdir -p "$K3S_STORAGE_TARGET/credentials"

log_info "K3s storage initialization complete"
exit 0
