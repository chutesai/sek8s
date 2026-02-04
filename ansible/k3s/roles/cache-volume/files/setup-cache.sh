#!/bin/bash
# setup-cache.sh - Set up HF cache dir for pod use (e.g. model weights)
# Creates /var/snap/cache with 1000:1000 (tdx on VM; matches runAsUser/runAsGroup in pod specs).
# system-manager (runs as chutes) gets write access by having chutes in group tdx (GID 1000); dir is 2775.
# All operations are idempotent: safe to run on reboot with an existing cache (mkdir -p, chown, chmod).
# On failure (e.g. /var/snap not mounted) the script shuts down the VM immediately; service also has OnFailure=poweroff.target.
set -euo pipefail

LOG_TAG="setup-cache"

log() {
    echo "$1"
    logger -t "$LOG_TAG" "$1" 2>/dev/null || true
}

log_error() {
    echo "ERROR: $1" >&2
    logger -t "$LOG_TAG" -p user.err "ERROR: $1" 2>/dev/null || true
}

# UID:GID for pod/container access (must match runAsUser/runAsGroup in pod spec). On VM this is tdx.
SNAP_OWNER="1000:1000"
SNAP_CACHE="/var/snap/cache"

# Ensure HF cache volume is mounted (without it we don't have enough room for model weights)
if ! mountpoint -q /var/snap; then
    log_error "/var/snap is not mounted, cannot set up cache"
    log_error "Shutting down immediately to prevent boot without HF cache"
    sync
    shutdown -h now
    exit 1
fi

# Create snap cache dir: 1000:1000 (tdx) so pods can use it; 2775 so group (tdx) can write; chutes is in group tdx so system-manager can write
mkdir -p "$SNAP_CACHE"
chown -R "$SNAP_OWNER" "$SNAP_CACHE"
chmod -R 2775 "$SNAP_CACHE"

log "Cache setup complete"
exit 0
