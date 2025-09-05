#!/bin/bash
set -e

# Log function
log() {
    echo "$1" >> /var/log/first-boot-k3s-label.log
}

# Configure k3s node label
log "Adding chutes labels to Kubernetes node..."
# Set KUBECONFIG for k3s
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
# Get node name (dynamic hostname)
NODE_NAME=$(hostname)
# Get node IP
NODE_IP=$(ip -4 addr show scope global | grep inet | awk '{print $2}' | cut -d'/' -f1 | head -n 1)
if [ -z "$NODE_IP" ]; then
    log "Failed to determine node IP, falling back to localhost"
    NODE_IP="127.0.0.1"
fi
# Wait for k3s to be ready (up to 60 seconds)
timeout 60 bash -c "until kubectl get nodes \"$NODE_NAME\" >/dev/null 2>&1; do sleep 1; done" || {
    log "Error: k3s not ready or node $NODE_NAME not found"
    exit 1
}
# Apply label with overwrite to mimic strategic-merge
kubectl label node "$NODE_NAME" chutes/external-ip="$NODE_IP" --overwrite && log "Labeled node $NODE_NAME with chutes/external-ip=$NODE_IP"

log "k3s node labeling completed."