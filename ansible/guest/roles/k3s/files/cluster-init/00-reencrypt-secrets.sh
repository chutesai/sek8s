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

# Replace each object individually, fetching the latest resourceVersion right
# before the replace to avoid Conflict errors caused by k3s-managed objects
# (e.g. k3s-serving TLS certs) being updated between the bulk GET and replace.
reencrypt_resources() {
    local kind="$1"
    local failed=0
    local pairs
    pairs=$(kubectl get "${kind}" --all-namespaces -o jsonpath='{range .items[*]}{.metadata.namespace}{" "}{.metadata.name}{"\n"}{end}')
    while IFS=' ' read -r ns name; do
        [[ -z "$name" ]] && continue
        local ok=false
        for attempt in 1 2 3; do
            if kubectl get "${kind}" -n "$ns" "$name" -o json | kubectl replace -f - 2>&1; then
                ok=true
                break
            fi
            sleep 0.5
        done
        if [[ "$ok" != true ]]; then
            log "ERROR: Failed to re-encrypt ${kind} ${ns}/${name} after 3 attempts"
            failed=1
        fi
    done <<< "$pairs"
    return "$failed"
}

if ! reencrypt_resources secrets; then
    log "ERROR: Re-encryption of secrets failed"
    exit 1
fi
if ! reencrypt_resources configmaps; then
    log "ERROR: Re-encryption of configmaps failed"
    exit 1
fi
log "All live records re-written through the active encryption provider"

# Purge kine history and scrub old_value.
#
# kine is append-only: every write appends a new row, leaving the previous
# revision as a dead row.  Additionally, every live row carries an old_value
# column with the previous (potentially plaintext) value used only for etcd
# watch event payloads — Kubernetes controllers do not rely on it.
#
# Both steps run online; SQLite WAL mode allows concurrent access with k3s.
#
# The marker is written only after this purge succeeds.  A failed purge causes
# a full retry on the next boot — plaintext left in dead rows or old_value
# is a security issue and must not be silently skipped.  The kubectl replace
# retry is safe: already-encrypted values are decrypted and re-encrypted
# idempotently, producing only additional dead rows for the next purge to clean.
STATE_DB="/var/lib/rancher/k3s/server/db/state.db"
if [[ -f "$STATE_DB" ]]; then
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
print(f"Deleted {dead} dead rows, nulled old_value on {nulled} live rows")
PYEOF
    then
        log "Kine purge complete — plaintext logically removed from state.db"
    else
        log "ERROR: Kine purge failed — not marking complete, will retry on next boot"
        exit 1
    fi
fi

touch "$MARKER"
