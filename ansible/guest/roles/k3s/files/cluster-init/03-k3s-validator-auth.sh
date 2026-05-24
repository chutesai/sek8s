#!/bin/bash
# 03-k3s-validator-auth.sh: Create or update the validator-auth K8s Secret
# and restart the attestation proxy to pick up the new per-VM ephemeral auth key.
#
# Runs every boot (NO .completed marker) because the ephemeral validator auth key
# rotates on each boot attestation.  The kine/etcd database persists across reboots,
# so the Secret from a previous boot must be replaced with the current ephemeral SS58.
#
# The per-VM ephemeral SS58 is written to /run/chutes/validator-ss58 (tmpfs) by the
# initramfs write-validator-auth script, which reads it from the boot attestation API
# response field vm_auth_ss58 in fetch_key_and_unlock (init-premount).
#
# Security model:
#   - The ephemeral SS58 rotates on every boot — no long-lived validator key in the VM.
#   - The delivery path (fetch_key_and_unlock) is measured into RTMR2 (initramfs).
#   - This script itself is measured into RTMR3 (via /usr/local/bin/k3s-init-scripts).
#   - The Secret is created at runtime and NOT in the measured manifest directory.
set -euo pipefail

LOG_FILE="/var/log/k3s-cluster-init.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [03-k3s-validator-auth] $1" | tee -a "$LOG_FILE"
}

VALIDATOR_SS58_FILE="/run/chutes/validator-ss58"

if [ ! -f "$VALIDATOR_SS58_FILE" ]; then
    log "ERROR: $VALIDATOR_SS58_FILE not found — initramfs write-validator-auth may have failed"
    exit 1
fi

VALIDATOR_SS58=$(cat "$VALIDATOR_SS58_FILE")

if [ -z "$VALIDATOR_SS58" ]; then
    log "ERROR: validator-ss58 is empty"
    exit 1
fi

log "Creating/updating validator-auth Secret with ephemeral SS58 (${VALIDATOR_SS58:0:12}...)"

# Use apply (not create) to handle both first boot (Secret absent) and subsequent boots
# (Secret exists with previous boot's SS58).  kubectl create --dry-run=client -o yaml
# generates the manifest; kubectl apply -f - creates or patches it in place.
kubectl create secret generic validator-auth \
    --from-literal=allowed-validators="$VALIDATOR_SS58" \
    -n attestation-system \
    --dry-run=client -o yaml | kubectl apply -f -

log "validator-auth Secret updated"

# Secrets consumed via secretKeyRef (env vars) are injected once at pod creation.
# Updating the Secret does NOT cause k3s to restart pods automatically.
# We must explicitly restart the attestation-proxy DaemonSet so it picks up the
# new ALLOWED_VALIDATORS value from the updated Secret.
log "Restarting attestation-proxy DaemonSet to apply new validator auth key..."
kubectl rollout restart daemonset/attestation-proxy -n attestation-system

log "attestation-proxy rollout restart triggered"
