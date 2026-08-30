#!/bin/bash
set -e

MARKER_DIR="${MARKER_DIR:-/var/lib/rancher/k3s/init-markers}"
MARKER_FILE="${MARKER_DIR}/03-k3s-miner-credentials.sh.completed"

# Run-once: skip if already completed
if [ -f "$MARKER_FILE" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Already completed, skipping" | tee -a /var/log/first-boot-miner-credentials.log
    exit 0
fi

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a /var/log/first-boot-miner-credentials.log
}

CREDENTIALS_DIR="/var/config"

log "Loading miner credentials..."
MINER_SS58=$(cat "$CREDENTIALS_DIR/miner-ss58")
log "Loaded miner ss58..."
MINER_SEED=$(cat "$CREDENTIALS_DIR/miner-seed")
log "Loaded miner seed..."

log "Creating miner credentials secret..."
kubectl create secret generic miner-credentials \
  --from-literal=ss58=$MINER_SS58 \
  --from-literal=seed=$MINER_SEED \
  -n chutes

# attestation-system also needs the seed: the attestation proxy signs each response with the
# miner hotkey (rc proof-of-possession) so the validator can authorize release-candidate
# measurements at runtime.
kubectl create secret generic miner-credentials \
  --from-literal=ss58=$MINER_SS58 \
  --from-literal=seed=$MINER_SEED \
  -n attestation-system

touch "$MARKER_FILE"
log "Successfully created miner credentials."