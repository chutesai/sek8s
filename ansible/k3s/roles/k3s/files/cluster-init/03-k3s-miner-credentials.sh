#!/bin/bash
set -e

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a /var/log/first-boot-miner-credentials.log
}

log "Loading miner credentials..."
MINER_SS58=$(cat ~/miner-ss58)
log "Loaded miner ss58..."
MINER_SEED=$(cat ~/miner-seed)
log "Loaded miner seed..."

log "Creating miner credentials secret..."
kubectl create secret generic miner-credentials \
  --from-literal=ss58=$MINER_SS58 \
  --from-literal=seed=$MINER_SEED \
  -n chutes

log "Successfully created miner credentials."