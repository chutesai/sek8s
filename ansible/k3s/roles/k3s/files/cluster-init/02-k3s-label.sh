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
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Detected public IP from $service: $public_ip" >&2
            echo "$public_ip"
            return 0
        fi
    done
    
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Warning: Could not detect public IP address" >&2
    return 1
}

# Function to apply and verify a label with retry logic
apply_and_verify_label() {
    local node_name=$1
    local label_key=$2
    local label_value=$3
    
    for attempt in {1..3}; do
        kubectl label node "$node_name" "$label_key=$label_value" --overwrite
        
        # Verify the label was applied correctly
        local actual_value=$(kubectl get node "$node_name" -o jsonpath="{.metadata.labels['$label_key']}" 2>/dev/null || echo "")
        
        if [[ "$actual_value" == "$label_value" ]]; then
            log "Labeled node $node_name with $label_key=$label_value"
            return 0
        fi
        
        log "Label verification failed for $label_key (attempt $attempt/3)"
        sleep 2
    done
    
    log "ERROR: Failed to apply and verify label $label_key=$label_value after 3 attempts"
    return 1
}

# Main execution
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

# Apply labels with verification and retry
apply_and_verify_label "$NODE_NAME" "chutes/external-ip" "$NODE_IP" || exit 1
apply_and_verify_label "$NODE_NAME" "chutes/tee" "true" || exit 1

log "k3s node labeling completed."