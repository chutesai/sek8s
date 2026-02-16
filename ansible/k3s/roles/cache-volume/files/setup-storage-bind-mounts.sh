#!/bin/bash
# setup-storage-bind-mounts.sh - Sync root filesystem data to storage volume and create bind mounts.
# Runs AFTER verify-storage.service, BEFORE k3s/containerd.
set -euo pipefail

LOG_TAG="setup-storage-bind-mounts"

log() {
    echo "$1"
    logger -t "$LOG_TAG" "$1" 2>/dev/null || true
}

STORAGE_BASE="/cache/storage"

# Ensure storage volume is mounted
if ! mountpoint -q "$STORAGE_BASE"; then
    log "ERROR: $STORAGE_BASE is not mounted, cannot create bind mounts"
    exit 1
fi

# --- Helpers ---

# Sync root filesystem content to storage if the storage directory is empty.
# Args: $1 = root fs path (source), $2 = storage path (destination)
sync_if_empty() {
    local root_path="$1"
    local storage_path="$2"

    mkdir -p "$storage_path"
    mkdir -p "$root_path"

    local file_count
    file_count=$(find "$storage_path" -mindepth 1 -maxdepth 1 ! -name "lost+found" 2>/dev/null | wc -l)
    if [[ "$file_count" -eq 0 ]]; then
        log "Storage dir $storage_path is empty, syncing from $root_path"
        if rsync -a --exclude='lost+found' "$root_path/" "$storage_path/"; then
            log "Synced $root_path -> $storage_path ($(du -sh "$storage_path" 2>/dev/null | cut -f1))"
        else
            log "ERROR: Failed to sync $root_path to $storage_path"
            exit 1
        fi
    fi
}

# Create a bind mount from storage to target, skipping if already mounted correctly.
# Args: $1 = storage path (source), $2 = target mount path
create_bind_mount() {
    local source="$1"
    local target="$2"

    mkdir -p "$source"
    mkdir -p "$target"

    if mountpoint -q "$target"; then
        if [ "$(stat -c %d "$target")" = "$(stat -c %d "$source")" ]; then
            log "Bind mount already correct: $source -> $target"
        else
            log "ERROR: $target is mounted but not our bind mount (unexpected device). Cannot continue."
            exit 1
        fi
    else
        log "Creating bind mount: $source -> $target"
        if mount --bind "$source" "$target"; then
            log "Bind mount created: $source -> $target"
        else
            log "ERROR: Failed to create bind mount: $source -> $target"
            exit 1
        fi
    fi
}

# --- Storage volume layout ---
#
# Each entry: storage_subdir root_fs_path
#
# On first boot (storage empty), root_fs_path is synced to storage_subdir.
# Then storage_subdir is bind-mounted over root_fs_path so all runtime writes go to storage.

MOUNTS=(
    "k3s                        /var/lib/rancher/k3s"
    "rancher-config             /etc/rancher/k3s"
    "kubelet                    /var/lib/kubelet"
    "admission-controller-certs /etc/admission-controller/certs"
    "chutes-agent               /var/lib/chutes/agent"
)

for entry in "${MOUNTS[@]}"; do
    read -r storage_subdir root_path <<< "$entry"
    storage_path="${STORAGE_BASE}/${storage_subdir}"
    sync_if_empty "$root_path" "$storage_path"
    create_bind_mount "$storage_path" "$root_path"
done

# --- Post-mount fixups for specific volumes ---

# k3s: ensure required subdirectories exist
mkdir -p "${STORAGE_BASE}/k3s/init-markers"
mkdir -p "${STORAGE_BASE}/k3s/credentials"

# chutes-agent: pod-friendly ownership (must match runAsUser/runAsGroup in pod spec)
chown -R 1000:1000 "${STORAGE_BASE}/chutes-agent"
chmod -R 755 "${STORAGE_BASE}/chutes-agent"

log "Bind mounts setup complete"
exit 0
