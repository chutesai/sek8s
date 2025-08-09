#!/bin/bash
set -e

# Log function
log() {
    echo "$1" >> /var/log/first-boot-k3s-config.log
}
# Set KUBECONFIG
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

CURRENT_HOSTNAME=$(hostname)

# Step 1: Start k3s if not running and drain the current node
if ! systemctl is-active --quiet k3s; then
    log "Starting k3s service..."
    systemctl start k3s
    # Wait for k3s to be ready
    timeout 60 bash -c "until kubectl get nodes \"$CURRENT_HOSTNAME\" >/dev/null 2>&1; do sleep 2; done" || {
        log "Warning: k3s not ready after 60 seconds, proceeding anyway"
    }
fi

CURRENT_NODE=$(kubectl get nodes -o=custom-columns=NAME:.metadata.name --no-headers)

NEW_NODE=$CURRENT_HOSTNAME

# Get node IP
log "Determining node IP..."
NODE_IP=$(ip -4 addr show scope global | grep inet | awk '{print $2}' | cut -d'/' -f1 | head -n 1)
if [ -z "$NODE_IP" ]; then
    log "Failed to determine node IP, falling back to localhost"
    NODE_IP="127.0.0.1"
fi
log "Node IP set to $NODE_IP"

# Drain the current node
log "Draining node $CURRENT_NODE..."
kubectl drain "$CURRENT_NODE" --ignore-daemonsets --delete-emptydir-data --force --timeout=60s || {
    log "Warning: Failed to drain node cleanly, proceeding with forced shutdown"
}

# Step 2: Stop k3s service
log "Stopping k3s service..."
systemctl stop k3s

# Step 4: Update k3s configuration
log "Updating k3s configuration..."
CONFIG_FILE="/etc/rancher/k3s/config.yaml"
mkdir -p /etc/rancher/k3s

# Update k3s config.yaml
if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" << EOF
node-name: $NEW_NODE
node-ip: $NODE_IP
node-external-ip: $NODE_IP
advertise-address: $NODE_IP
EOF
else
    # Ensure config file ends with newline before appending
    [ -s "$CONFIG_FILE" ] && [ "$(tail -c1 "$CONFIG_FILE")" != "" ] && echo "" >> "$CONFIG_FILE"

    # Update existing config - check if each field exists before updating
    if grep -q '^node-name:' "$CONFIG_FILE"; then
        sed -i "s/^node-name:.*/node-name: $NEW_NODE/" "$CONFIG_FILE"
    else
        echo "node-name: $NEW_NODE" >> "$CONFIG_FILE"
    fi
    
    if grep -q '^node-ip:' "$CONFIG_FILE"; then
        sed -i "s/^node-ip:.*/node-ip: $NODE_IP/" "$CONFIG_FILE"
    else
        echo "node-ip: $NODE_IP" >> "$CONFIG_FILE"
    fi
    
    if grep -q '^node-external-ip:' "$CONFIG_FILE"; then
        sed -i "s/^node-external-ip:.*/node-external-ip: $NODE_IP/" "$CONFIG_FILE"
    else
        echo "node-external-ip: $NODE_IP" >> "$CONFIG_FILE"
    fi
    
    if grep -q '^advertise-address:' "$CONFIG_FILE"; then
        sed -i "s/^advertise-address:.*/advertise-address: $NODE_IP/" "$CONFIG_FILE"
    else
        echo "advertise-address: $NODE_IP" >> "$CONFIG_FILE"
    fi
fi

# Update k3s service with TLS SAN
K3S_SERVICE_FILE="/etc/systemd/system/k3s.service"
if [ -f "$K3S_SERVICE_FILE" ]; then
    if grep -q '^ExecStart=.*--tls-san' "$K3S_SERVICE_FILE"; then
        sed -i "s/--tls-san [^ ]*/--tls-san $NODE_IP/" "$K3S_SERVICE_FILE"
    else
        sed -i "/^ExecStart=/ s|$| --tls-san $NODE_IP|" "$K3S_SERVICE_FILE"
    fi
    systemctl daemon-reload
fi

# Step 5: Restart k3s service
log "Restarting k3s service with new configuration..."
systemctl start k3s

# Wait for new node to be ready
log "Waiting for new node $NEW_NODE to be ready..."
timeout 120 bash -c "until kubectl get nodes \"$NEW_NODE\" >/dev/null 2>&1; do sleep 2; done" || {
    log "Error: New node not ready after 120 seconds"
    exit 1
}

# Step 6: Delete old node if it exists and is different
if [ "$CURRENT_NODE" != "$NEW_NODE" ] && kubectl get node "$NEW_NODE" >/dev/null 2>&1; then
    log "Deleting old node $CURRENT_NODE..."
    kubectl delete node "$CURRENT_NODE" || log "Warning: Failed to delete old node"
fi

log "k3s hostname update completed successfully: $CURRENT_NODE -> $NEW_NODE"