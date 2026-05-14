#!/bin/bash
# 00-reencrypt-secrets.sh — Re-encrypt any plaintext secrets in state.db.
#
# Runs first in the cluster-init sequence (00- prefix).  Uses a marker so it
# only runs once — on the first boot where secrets encryption is active.
#
# Why this is needed:
#   On first boot of a fresh VM, setup-storage-bind-mounts deletes the
#   build-time state.db so k3s starts fresh with encryption from the first
#   write.  On an upgrade from an image that did not have secrets encryption,
#   state.db already exists with plaintext secrets.  This script detects that
#   case and re-writes every secret through the active encryption provider.
#
# The EncryptionConfiguration has secretbox first, identity last:
#   - Reads:  secretbox attempted first; identity fallback decrypts plaintext
#   - Writes: always use secretbox (first provider)
# So `kubectl replace` re-encrypts every secret in place without data loss.
set -euo pipefail

MARKER="${MARKER_DIR:-/var/lib/rancher/k3s/init-markers}/reencrypt-secrets.done"
LOG_FILE="/var/log/k3s-cluster-init.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [00-reencrypt-secrets] $1" | tee -a "$LOG_FILE"
}

if [[ -f "$MARKER" ]]; then
    log "Re-encryption marker exists — skipping"
    exit 0
fi

ENCRYPTION_CONFIG="/run/chutes/k3s-encryption-config.yaml"
if [[ ! -f "$ENCRYPTION_CONFIG" ]]; then
    log "No encryption config at $ENCRYPTION_CONFIG — skipping (encryption not active)"
    touch "$MARKER"
    exit 0
fi

# Check encryption is actually using secretbox (not identity-only)
if ! grep -q "secretbox" "$ENCRYPTION_CONFIG"; then
    log "Encryption config present but identity-only — skipping"
    touch "$MARKER"
    exit 0
fi

log "Secrets encryption is active — re-encrypting all existing plaintext secrets and configmaps..."

log "Replacing all secrets and configmaps so live kine rows have encrypted values..."
if ! kubectl get secrets --all-namespaces -o json | kubectl replace -f -; then
    log "ERROR: Re-encryption of secrets failed"
    exit 1
fi
if ! kubectl get configmaps --all-namespaces -o json | kubectl replace -f -; then
    log "ERROR: Re-encryption of configmaps failed"
    exit 1
fi
log "All live records re-written through the active encryption provider"

# Mark done before the purge so a restart failure doesn't re-run this on
# next boot (live records are already encrypted).
touch "$MARKER"

# Purge kine history and scrub old_value, then VACUUM.
#
# kine is append-only: it never SQLite-DELETEs old revision rows; they
# accumulate as dead rows in the table.  Additionally, every live row
# carries an old_value column with the previous (potentially plaintext)
# value — VACUUM cannot remove a column from a live row.
#
# Steps (k3s must be stopped for an exclusive lock):
#   1. DELETE all non-current kine rows (id != MAX(id) per name).
#   2. NULL out old_value on all remaining live rows — old_value is only
#      used as the previous-value payload in etcd watch events; Kubernetes
#      controllers do not rely on it for correctness.
#   3. VACUUM to physically reclaim the freed pages.
STATE_DB="/var/lib/rancher/k3s/server/db/state.db"
if [[ -f "$STATE_DB" ]]; then
    log "Stopping k3s to purge kine history and VACUUM state.db..."
    systemctl stop k3s

    if python3 - "$STATE_DB" <<'PYEOF'
import sqlite3, sys
db = sys.argv[1]
conn = sqlite3.connect(db)
dead = conn.execute(
    "DELETE FROM kine WHERE id NOT IN (SELECT MAX(id) FROM kine GROUP BY name)"
).rowcount
nulled = conn.execute("UPDATE kine SET old_value = NULL").rowcount
conn.commit()
conn.close()
conn = sqlite3.connect(db, isolation_level=None)
conn.execute("VACUUM")
conn.close()
print(f"Deleted {dead} dead rows, nulled old_value on {nulled} live rows; VACUUM complete")
PYEOF
    then
        log "Kine purge and VACUUM complete — no plaintext remains in state.db"
    else
        log "WARNING: Kine purge/VACUUM failed (non-fatal, live records are encrypted)"
    fi

    log "Restarting k3s..."
    systemctl start k3s
fi
