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

# Always sync static files from image to storage so new VM images refresh manifests/registries
# without deleting the storage volume.
always_sync_manifests() {
    local src="/var/lib/rancher/k3s/server/manifests"
    local dest="${STORAGE_BASE}/k3s/server/manifests"
    if [[ -d "$src" ]]; then
        mkdir -p "$dest"
        if rsync -a --delete "$src/" "$dest/"; then
            log "Synced manifests: $src -> $dest"
        else
            log "ERROR: Failed to sync manifests"
            exit 1
        fi
    fi
}

# Always sync admission controller certs so manifest caBundle and cert stay paired.
always_sync_admission_certs() {
    local src="/etc/admission-controller/certs"
    local dest="${STORAGE_BASE}/admission-controller-certs"
    if [[ -d "$src" ]]; then
        mkdir -p "$dest"
        if rsync -a --delete "$src/" "$dest/"; then
            log "Synced admission certs: $src -> $dest"
        else
            log "ERROR: Failed to sync admission certs"
            exit 1
        fi
    fi
}

# Evict admission webhooks from kine so k3s can bootstrap without the admission controller
# blocking internal operations (the service isn't running yet at boot). k3s will re-create
# them from the static manifests after startup completes.
evict_stale_webhooks() {
    local db="${STORAGE_BASE}/k3s/server/db/state.db"
    if [[ -f "$db" ]]; then
        if python3 - "$db" <<'PYEOF'
import sqlite3
import sys
db = sys.argv[1]
conn = sqlite3.connect(db)
for resource_type, name in [
    ("validatingwebhookconfigurations", "admission-controller-webhook"),
    ("mutatingwebhookconfigurations", "admission-controller-mutating-webhook"),
]:
    key = f"/registry/{resource_type}/{name}"
    cur = conn.execute("DELETE FROM kine WHERE name = ?", (key,))
    if cur.rowcount:
        print(f"Evicted {key}")
conn.commit()
conn.close()
PYEOF
        then
            log "Evicted stale admission webhooks from kine"
        else
            log "WARNING: Failed to evict admission webhooks from kine"
        fi
    fi
}

# Evict k3s addon tracking entries from kine so the deploy controller re-applies all static
# manifests on every boot. k3s tracks applied manifests via Addon CRs with content hashes;
# deleting these forces a fresh apply on next start. Running unconditionally is safe because
# re-applying manifests is idempotent, and it avoids edge cases where file sync completed on
# a prior boot but addon tracking was never cleared.
evict_stale_addons() {
    local db="${STORAGE_BASE}/k3s/server/db/state.db"
    if [[ -f "$db" ]]; then
        if python3 - "$db" <<'PYEOF'
import sqlite3
import sys
db = sys.argv[1]
conn = sqlite3.connect(db)
cur = conn.execute("DELETE FROM kine WHERE name LIKE '/registry/k3s.cattle.io/addons/%'")
print(f"Evicted {cur.rowcount} addon tracking entries")
conn.commit()
conn.close()
PYEOF
        then
            log "Evicted stale addon tracking from kine (k3s will re-apply all static manifests)"
        else
            log "WARNING: Failed to evict addon tracking from kine"
        fi
    fi
}

always_sync_registries() {
    local src="/etc/rancher/k3s/registries.yaml"
    local dest="${STORAGE_BASE}/rancher-config/registries.yaml"
    if [[ -f "$src" ]]; then
        mkdir -p "${STORAGE_BASE}/rancher-config"
        if cp -a "$src" "$dest"; then
            log "Synced registries.yaml: $src -> $dest"
        else
            log "ERROR: Failed to sync registries.yaml"
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
    sync_if_empty "$root_path" "${STORAGE_BASE}/${storage_subdir}"
done

# Refresh static image files on every boot so new VM images update manifests/registries/certs.
always_sync_manifests
always_sync_registries
always_sync_admission_certs

# Evict webhooks so k3s can bootstrap, then evict addon tracking so it re-applies all manifests.
evict_stale_webhooks
evict_stale_addons

for entry in "${MOUNTS[@]}"; do
    read -r storage_subdir root_path <<< "$entry"
    create_bind_mount "${STORAGE_BASE}/${storage_subdir}" "$root_path"
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
