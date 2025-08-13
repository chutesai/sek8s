#!/bin/bash
# /usr/local/bin/k3s-init.sh
# k3s-init: Start k3s, drain any old nodes, reconfigure, restart
set -e

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a /var/log/k3s-init.log
}

log "Starting k3s initialization..."

# Get current hostname and IP
HOSTNAME=$(hostname)
NODE_IP=$(ip -4 addr show scope global | grep -E "inet .* (eth|ens|enp)" | head -1 | awk '{print $2}' | cut -d'/' -f1)
if [ -z "$NODE_IP" ]; then
    NODE_IP=$(ip -4 addr show scope global | grep inet | awk '{print $2}' | cut -d'/' -f1 | head -n 1)
fi
log "Target hostname: $HOSTNAME, IP: $NODE_IP"

# STEP 1: Start k3s with minimal configuration to check for old nodes
log "Step 1: Starting k3s with temporary configuration..."
mkdir -p /etc/rancher/k3s

# Start with minimal config that doesn't specify node name (will use hostname)
cat > /etc/rancher/k3s/config.yaml << EOF
write-kubeconfig-mode: "0644"
disable:
  - traefik
  - servicelb
cluster-cidr: 10.42.0.0/16
service-cidr: 10.43.0.0/16
EOF

# Start k3s
systemctl start k3s

# Wait for k3s to be ready
log "Waiting for k3s to start..."
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
for i in {1..60}; do
    if kubectl get nodes >/dev/null 2>&1; then
        log "k3s is ready"
        break
    fi
    sleep 2
done

# STEP 2: Find any nodes that don't match current hostname
log "Step 2: Checking for old nodes..."
OLD_NODES=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep -v "^${HOSTNAME}$" || true)

if [ -z "$OLD_NODES" ]; then
    log "No old nodes found, checking if current node exists..."
    
    # Check if current hostname node already exists
    if kubectl get node "$HOSTNAME" >/dev/null 2>&1; then
        log "Current node $HOSTNAME already exists, just updating configuration"
        # Just update the config and restart
        systemctl stop k3s
        sleep 5
    else
        log "No nodes found or only current node exists"
    fi
else
    # Process each old node
    log "Found old node(s) to clean up: $OLD_NODES"
    
    for OLD_NODE in $OLD_NODES; do
        log "Draining old node: $OLD_NODE..."
        
        # Cordon first to prevent new pods
        kubectl cordon "$OLD_NODE" || true
        
        # Drain the node - this will evict all pods
        kubectl drain "$OLD_NODE" \
            --ignore-daemonsets \
            --delete-emptydir-data \
            --force \
            --grace-period=30 \
            --timeout=60s || true
        
        # Taint the node to ensure nothing schedules to it
        kubectl taint nodes "$OLD_NODE" node.kubernetes.io/unscheduled=true:NoSchedule --overwrite || true
        
        log "Node $OLD_NODE drained and tainted"
    done
    
    # Stop k3s for reconfiguration
    log "Stopping k3s for reconfiguration..."
    systemctl stop k3s
    sleep 5
fi

# STEP 3: Create new configuration with correct hostname and IP
log "Step 3: Creating new k3s configuration..."
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

# STEP 4: Start k3s with new configuration
log "Step 4: Starting k3s with new configuration..."
systemctl start k3s

# Wait for new node to be ready
log "Waiting for node $HOSTNAME to be ready..."
for i in {1..60}; do
    if kubectl get node "$HOSTNAME" >/dev/null 2>&1; then
        NODE_STATUS=$(kubectl get node "$HOSTNAME" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
        if [ "$NODE_STATUS" = "True" ]; then
            log "Node $HOSTNAME is ready"
            break
        fi
    fi
    sleep 2
done

# STEP 5: Delete any old nodes
if [ -n "$OLD_NODES" ]; then
    log "Step 5: Removing old node(s)..."
    for OLD_NODE in $OLD_NODES; do
        if kubectl get node "$OLD_NODE" >/dev/null 2>&1; then
            kubectl delete node "$OLD_NODE" || log "Failed to delete node $OLD_NODE"
            log "Removed old node: $OLD_NODE"
        fi
    done
fi

# Create marker file
mkdir -p /var/lib/rancher/k3s
touch /var/lib/rancher/k3s/.initialized

log "k3s initialization complete"