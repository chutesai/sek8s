#!/bin/bash
set -e

# Configuration
PUBLIC_IP_TIMEOUT=5
INCLUDE_PUBLIC_IP="true"

# Log function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a /var/log/first-boot-k3s-label.log
}

# Function to get public IP address
get_public_ip() {
    local public_ip=""
    
    # Skip if disabled
    if [[ "$INCLUDE_PUBLIC_IP" != "true" ]]; then
        log "Public IP detection disabled"
        return 1
    fi
    
    local services=(
        "ifconfig.me"
        "icanhazip.com" 
        "ipecho.net/plain"
        "checkip.amazonaws.com"
    )
    
    for service in "${services[@]}"; do
        public_ip=$(curl -s --max-time "$PUBLIC_IP_TIMEOUT" "$service" 2>/dev/null | grep -oE '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$' || true)
        if [[ -n "$public_ip" ]]; then
            log "Detected public IP from $service: $public_ip"
            echo "$public_ip"
            return 0
        fi
    done
    
    log "Warning: Could not detect public IP address from any service"
    return 1
}

# Configure k3s node label
log "Adding chutes labels to Kubernetes node..."

# Set KUBECONFIG for k3s
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Get node name (dynamic hostname)
NODE_NAME=$(hostname)

# Get public IP address
NODE_IP=$(get_public_ip)
if [ -z "$NODE_IP" ]; then
    log "Failed to determine public IP, attempting to fall back to local IP"
    NODE_IP=$(ip -4 addr show scope global | grep inet | awk '{print $2}' | cut -d'/' -f1 | head -n 1)
    if [ -z "$NODE_IP" ]; then
        log "Failed to determine any IP address"
        exit 1
    fi
    log "Using local IP as fallback: $NODE_IP"
fi

# Wait for k3s to be ready (up to 60 seconds)
log "Waiting for k3s node $NODE_NAME to be ready..."
timeout 60 bash -c "until kubectl get nodes \"$NODE_NAME\" >/dev/null 2>&1; do sleep 1; done" || {
    log "Error: k3s not ready or node $NODE_NAME not found"
    exit 1
}

# Apply label with overwrite to mimic strategic-merge
kubectl label node "$NODE_NAME" chutes/external-ip="$NODE_IP" --overwrite && log "Labeled node $NODE_NAME with chutes/external-ip=$NODE_IP"
kubectl label node "$NODE_NAME" chutes/tee="true" --overwrite && log "Labeled node $NODE_NAME with chutes/tee=\"true\""

log "k3s node labeling completed."