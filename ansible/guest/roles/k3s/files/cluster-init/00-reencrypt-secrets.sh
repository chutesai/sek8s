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

# Process each resource individually with a fresh fetch per attempt.
#
# A bulk `kubectl get --all-namespaces -o json | kubectl apply` has a race window:
# k3s-managed objects (validator-auth, k3s-serving, node-password, etc.) are
# updated by the addon controller concurrently during cluster init.  If the
# controller updates one between our bulk get and the apply, kubectl apply uses
# the stale resourceVersion from the snapshot and the API server rejects it with
# a 409 Conflict.
#
# Fix: fetch each resource immediately before applying it — the resourceVersion is
# always current at apply time.  On conflict, re-fetch and retry from scratch so
# we never replay a stale snapshot.  Resources deleted between list and get are
# skipped silently (gone = already re-encrypted or never needed).
reencrypt_resource_type() {
    local resource_type="$1"
    local max_attempts=5

    local items
    items=$(kubectl get "$resource_type" --all-namespaces \
        -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.metadata.name}{"\n"}{end}' \
        2>/dev/null) || true

    local failed=0
    while IFS=$'\t' read -r ns name; do
        [[ -z "$ns" || -z "$name" ]] && continue

        local attempt=1
        local ok=0
        while [[ $attempt -le $max_attempts ]]; do
            # Fresh fetch every attempt — never reuse a snapshot from a previous attempt
            local json
            json=$(kubectl get "$resource_type" "$name" -n "$ns" -o json 2>/dev/null) || {
                # Resource was deleted between list and get — skip it
                ok=1
                break
            }

            local err
            err=$(echo "$json" \
                | python3 -c "
import json, sys
d = json.load(sys.stdin)
d['metadata'].pop('resourceVersion', None)
print(json.dumps(d))
" | kubectl apply -f - 2>&1) && { ok=1; break; }

            if echo "$err" | grep -q "Conflict\|the object has been modified"; then
                log "WARN: conflict on $resource_type $ns/$name (attempt $attempt/$max_attempts) — re-fetching"
            else
                log "WARN: error on $resource_type $ns/$name (attempt $attempt/$max_attempts): $err"
            fi
            attempt=$((attempt + 1))
            [[ $attempt -le $max_attempts ]] && sleep 1
        done

        if [[ $ok -eq 0 ]]; then
            log "ERROR: failed to re-encrypt $resource_type $ns/$name after $max_attempts attempts"
            failed=$((failed + 1))
        fi
    done <<< "$items"

    return "$failed"
}

if ! reencrypt_resource_type "secrets"; then
    log "ERROR: Re-encryption of secrets failed"
    exit 1
fi
if ! reencrypt_resource_type "configmaps"; then
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
