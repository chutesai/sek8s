#!/bin/bash
# Reconcile the miner-credentials secret in every namespace that consumes it.
#
# Deliberately NOT run-once (no marker) and idempotent via `kubectl apply` upsert: the desired
# secret is fully determined by the per-VM config-volume creds + this image, so it should converge
# on every boot. That makes upgrades work — an image that adds a key (e.g. the attestation proxy's
# seed in attestation-system) updates the existing secret in place. This script runs as root with
# the admin kubeconfig, so it can upsert regardless of the miner's limited RBAC; miners never have
# to delete a secret or wipe their storage volume to pick up a new key.
#
# The secret carries ss58 plus exactly one key, the one on the config volume: privateKey (the
# 64-byte sr25519 key) or seed. `kubectl apply` only prunes keys an earlier apply set, and the
# chart creates this secret at image build (ss58/seed = REPLACE_ME) with no last-applied
# annotation, so the other key is removed explicitly — otherwise a private-key VM would keep the
# build-time seed. (A switch between keys is also pruned by apply's own record; the removal does
# not depend on it.)
set -euo pipefail

LOG_FILE="${LOG_FILE:-/var/log/first-boot-miner-credentials.log}"
CREDENTIALS_DIR="${CREDENTIALS_DIR:-/var/config}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Retry wrapper: this reconcile runs every boot, and the post-start orchestrator powers the VM off
# on any failed script, so a transient k8s API blip must not take the VM down.
retry_kubectl() {
    local attempts=10 delay=3 n=1
    until "$@"; do
        if [ "$n" -ge "$attempts" ]; then
            log "ERROR: kubectl failed after ${attempts} attempts: $*"
            return 1
        fi
        log "kubectl transient failure (attempt ${n}/${attempts}) — retrying in ${delay}s"
        sleep "$delay"
        n=$((n + 1))
    done
}

log "Loading miner credentials..."
# process-config.py has already required exactly one key; re-check rather than pick one.
if [ -f "$CREDENTIALS_DIR/miner-private-key" ] && [ ! -f "$CREDENTIALS_DIR/miner-seed" ]; then
    KEY=privateKey KEY_FILE=miner-private-key OTHER_KEY=seed
elif [ -f "$CREDENTIALS_DIR/miner-seed" ] && [ ! -f "$CREDENTIALS_DIR/miner-private-key" ]; then
    KEY=seed KEY_FILE=miner-seed OTHER_KEY=privateKey
else
    log "ERROR: $CREDENTIALS_DIR must hold exactly one of miner-private-key or miner-seed"
    exit 1
fi
MINER_SS58=$(cat "$CREDENTIALS_DIR/miner-ss58")
MINER_KEY=$(cat "$CREDENTIALS_DIR/$KEY_FILE")

# The upsert is a pipeline (client-side manifest gen | server-side apply); wrap it in a function so
# retry_kubectl can re-run the whole thing on a transient failure.
apply_miner_secret() {
    kubectl create secret generic miner-credentials \
      --from-literal=ss58="$MINER_SS58" \
      --from-literal="$KEY=$MINER_KEY" \
      --dry-run=client -o yaml | kubectl apply -n "$1" -f -
}

# JSON-patch the other key away when present. A remove of an absent key fails the whole patch,
# so check first; the secret was just applied and nothing else writes it during cluster init.
# Runs as retry_kubectl's `until` condition, where set -e is off: a failed get must return 1 (and
# be retried), not read as "key absent".
remove_other_key() {
    local present
    present=$(kubectl get secret miner-credentials -n "$1" -o "jsonpath={.data.$OTHER_KEY}") || return 1
    [ -n "$present" ] || return 0
    log "Removing ${OTHER_KEY} from miner-credentials in $1 (config volume carries ${KEY})"
    kubectl patch secret miner-credentials -n "$1" --type=json \
      -p "[{\"op\":\"remove\",\"path\":\"/data/${OTHER_KEY}\"}]"
}

# Upsert into each consuming namespace:
#   chutes             — miner workloads / control plane sign with the hotkey.
#   attestation-system — the attestation proxy signs responses with the hotkey (rc proof-of-
#                        possession) so the validator authorizes release-candidate measurements.
for ns in chutes attestation-system; do
    log "Reconciling miner-credentials secret in ${ns}..."
    retry_kubectl apply_miner_secret "$ns"
    retry_kubectl remove_other_key "$ns"
done

log "Miner credentials reconciled."
