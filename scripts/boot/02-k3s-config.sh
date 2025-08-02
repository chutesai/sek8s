#!/bin/bash
set -e

# Log function
log() {
    echo "$1" >> /var/log/first-boot-k3s-config.log
}

# Get node IP (equivalent to ansible_host)
log "Determining node IP..."
NODE_IP=$(ip -4 addr show scope global | grep inet | awk '{print $2}' | cut -d'/' -f1 | head -n 1)
if [ -z "$NODE_IP" ]; then
    log "Failed to determine node IP, falling back to localhost"
    NODE_IP="127.0.0.1"
fi
log "Node IP set to $NODE_IP"

# Configure k3s node name and IP settings
log "Configuring k3s node name and IP settings..."
# Get node name (dynamic hostname)
NODE_NAME=$(hostname)
# Set node name and IP settings in k3s config.yaml
CONFIG_FILE="/etc/rancher/k3s/config.yaml"
if [ ! -f "$CONFIG_FILE" ]; then
    mkdir -p /etc/rancher/k3s
    cat > "$CONFIG_FILE" << EOF
node-name: $NODE_NAME
node-ip: $NODE_IP
node-external-ip: $NODE_IP
advertise-address: $NODE_IP
EOF
    log "Created $CONFIG_FILE with node-name: $NODE_NAME and IP settings"
else
    # Update or append node-name
    if grep -q '^node-name:' "$CONFIG_FILE"; then
        sed -i "s/^node-name:.*/node-name: $NODE_NAME/" "$CONFIG_FILE"
        log "Updated node-name to $NODE_NAME in $CONFIG_FILE"
    else
        echo "node-name: $NODE_NAME" >> "$CONFIG_FILE"
        log "Appended node-name: $NODE_NAME to $CONFIG_FILE"
    fi
    # Update or append node-ip
    if grep -q '^node-ip:' "$CONFIG_FILE"; then
        sed -i "s/^node-ip:.*/node-ip: $NODE_IP/" "$CONFIG_FILE"
        log "Updated node-ip to $NODE_IP in $CONFIG_FILE"
    else
        echo "node-ip: $NODE_IP" >> "$CONFIG_FILE"
        log "Appended node-ip: $NODE_IP to $CONFIG_FILE"
    fi
    # Update or append node-external-ip
    if grep -q '^node-external-ip:' "$CONFIG_FILE"; then
        sed -i "s/^node-external-ip:.*/node-external-ip: $NODE_IP/" "$CONFIG_FILE"
        log "Updated node-external-ip to $NODE_IP in $CONFIG_FILE"
    else
        echo "node-external-ip: $NODE_IP" >> "$CONFIG_FILE"
        log "Appended node-external-ip: $NODE_IP to $CONFIG_FILE"
    fi
    # Update or append advertise-address
    if grep -q '^advertise-address:' "$CONFIG_FILE"; then
        sed -i "s/^advertise-address:.*/advertise-address: $NODE_IP/" "$CONFIG_FILE"
        log "Updated advertise-address to $NODE_IP in $CONFIG_FILE"
    else
        echo "advertise-address: $NODE_IP" >> "$CONFIG_FILE"
        log "Appended advertise-address: $NODE_IP to $CONFIG_FILE"
    fi
fi

# Update k3s service definition with --tls-san
K3S_SERVICE_FILE="/etc/systemd/system/k3s.service"
if [ -f "$K3S_SERVICE_FILE" ]; then
    if grep -q '^ExecStart=.*--tls-san' "$K3S_SERVICE_FILE"; then
        sed -i "s/--tls-san [^ ]*/--tls-san $NODE_IP/" "$K3S_SERVICE_FILE"
        log "Updated --tls-san to $NODE_IP in $K3S_SERVICE_FILE"
    else
        sed -i "/^ExecStart=/ s|$| --tls-san $NODE_IP|" "$K3S_SERVICE_FILE"
        log "Appended --tls-san $NODE_IP to ExecStart in $K3S_SERVICE_FILE"
    fi
else
    log "Error: $K3S_SERVICE_FILE not found"
    exit 1
fi

# Reload systemd and restart k3s
systemctl daemon-reload
if systemctl is-active --quiet k3s; then
    log "Restarting k3s to apply node name, IP settings, and TLS SAN..."
    systemctl restart k3s
else
    log "Starting k3s service..."
    systemctl start k3s
fi

log "k3s configuration completed."