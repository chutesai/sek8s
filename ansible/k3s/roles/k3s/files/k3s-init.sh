#!/bin/bash
# /usr/local/bin/k3s-init.sh
# k3s-init: Configure k3s with dynamic IP and hostname
set -e

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a /var/log/k3s-init.log
}

log "Starting k3s initialization..."

# Wait for hostname to be set (by cloud-init or manually)
HOSTNAME=$(hostname)
log "Using hostname: $HOSTNAME"

# Get node IP
NODE_IP=$(ip -4 addr show scope global | grep -E "inet .* (eth|ens|enp)" | head -1 | awk '{print $2}' | cut -d'/' -f1)
if [ -z "$NODE_IP" ]; then
    NODE_IP=$(ip -4 addr show scope global | grep inet | awk '{print $2}' | cut -d'/' -f1 | head -n 1)
fi
if [ -z "$NODE_IP" ]; then
    log "Warning: Failed to determine node IP, using 127.0.0.1"
    NODE_IP="127.0.0.1"
fi
log "Using IP: $NODE_IP"

# Directory should already exist from build, but ensure it's there
# This handles both cases: fresh build or if directory was somehow removed
mkdir -p /etc/rancher/k3s

# Create k3s configuration with dynamic values
# This overwrites any placeholder config from the build
log "Writing k3s configuration..."
cat > /etc/rancher/k3s/config.yaml << EOF
node-name: $HOSTNAME
node-ip: $NODE_IP
node-external-ip: $NODE_IP
advertise-address: $NODE_IP
bind-address: 0.0.0.0
tls-san:
  - $NODE_IP
  - $HOSTNAME
  - localhost
  - 127.0.0.1
write-kubeconfig-mode: "0644"
disable:
  - traefik
  - servicelb
cluster-cidr: 10.42.0.0/16
service-cidr: 10.43.0.0/16
EOF

# Create marker file
mkdir -p /var/lib/rancher/k3s
touch /var/lib/rancher/k3s/.initialized
log "k3s initialization complete"