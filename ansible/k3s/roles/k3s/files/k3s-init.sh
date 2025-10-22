#!/bin/bash
# /usr/local/bin/k3s-init.sh
# k3s-init: Start k3s DIRECTLY as a process, drain old nodes, then stop it
set -e

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a /var/log/k3s-init.log
}

# Function to notify systemd we're still alive
notify_systemd() {
    if [ -n "$NOTIFY_SOCKET" ]; then
        systemd-notify --status="$1" || true
    fi
}

# Public IP detection configuration
INCLUDE_PUBLIC_IP="${INCLUDE_PUBLIC_IP:-true}"
PUBLIC_IP_TIMEOUT="${PUBLIC_IP_TIMEOUT:-5}"
USE_PUBLIC_IP_FOR_ADVERTISE="${USE_PUBLIC_IP_FOR_ADVERTISE:-true}"

# Function to get public IP address
get_public_ip() {
    local public_ip=""
    
    # Skip if disabled
    if [[ "$INCLUDE_PUBLIC_IP" != "true" ]]; then
        return 0
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
    
    log "Warning: Could not detect public IP address"
    return 1
}

log "Starting k3s initialization..."

# Tell systemd we're starting
notify_systemd "Starting k3s initialization"

# Get current hostname and local IP
HOSTNAME=$(hostname)
NODE_IP=$(ip -4 addr show scope global | grep -E "inet .* (eth|ens|enp)" | head -1 | awk '{print $2}' | cut -d'/' -f1)
if [ -z "$NODE_IP" ]; then
    NODE_IP=$(ip -4 addr show scope global | grep inet | awk '{print $2}' | cut -d'/' -f1 | head -n 1)
fi
log "Target hostname: $HOSTNAME, Local IP: $NODE_IP"

# Get public IP
log "Detecting public IP..."
PUBLIC_IP=$(get_public_ip)
if [[ -n "$PUBLIC_IP" ]]; then
    log "Public IP detected: $PUBLIC_IP"
    
    # Decide which IP to use for advertise-address
    if [[ "$USE_PUBLIC_IP_FOR_ADVERTISE" == "true" ]]; then
        ADVERTISE_IP="$PUBLIC_IP"
        EXTERNAL_IP="$PUBLIC_IP"
        log "Using public IP for advertise-address"
    else
        ADVERTISE_IP="$NODE_IP"
        EXTERNAL_IP="$PUBLIC_IP"
        log "Using local IP for advertise-address, public IP as external-ip"
    fi
else
    log "No public IP detected, using local IP"
    ADVERTISE_IP="$NODE_IP"
    EXTERNAL_IP="$NODE_IP"
fi

# Create k3s configuration with comprehensive TLS SANs
log "Creating k3s configuration with TLS SANs..."
mkdir -p /etc/rancher/k3s

# Build TLS SAN list
TLS_SANS=(
    "$NODE_IP"
    "$HOSTNAME"
    "localhost" 
    "127.0.0.1"
    "::1"
)

# Add public IP to TLS SANs if detected and different from local IP
if [[ -n "$PUBLIC_IP" ]] && [[ "$PUBLIC_IP" != "$NODE_IP" ]]; then
    TLS_SANS+=("$PUBLIC_IP")
    log "Added public IP to TLS SANs: $PUBLIC_IP"
fi

# Create the k3s config with all TLS SANs
cat > /etc/rancher/k3s/config.yaml << EOF
node-name: $HOSTNAME
node-ip: $NODE_IP
node-external-ip: $EXTERNAL_IP
advertise-address: $ADVERTISE_IP
tls-san:
EOF

# Add each TLS SAN to the config
for san in "${TLS_SANS[@]}"; do
    echo "  - $san" >> /etc/rancher/k3s/config.yaml
done

# Continue with the rest of the config
cat >> /etc/rancher/k3s/config.yaml << EOF
write-kubeconfig-mode: "0644"
disable:
  - traefik
  - servicelb
cluster-cidr: 10.42.0.0/16
service-cidr: 10.43.0.0/16
EOF

# Log the configuration for debugging
log "k3s configuration created with the following settings:"
log "  node-name: $HOSTNAME"
log "  node-ip: $NODE_IP" 
log "  node-external-ip: $EXTERNAL_IP"
log "  advertise-address: $ADVERTISE_IP"
log "  TLS SANs: ${TLS_SANS[*]}"

# Start k3s directly as a background process
log "Starting k3s process directly..."
/usr/local/bin/k3s server > /var/log/k3s-init-process.log 2>&1 &
K3S_PID=$!
log "Started k3s with PID: $K3S_PID"

# Function to check if k3s is still running
check_k3s_alive() {
    if ! kill -0 $K3S_PID 2>/dev/null; then
        log "ERROR: k3s process died unexpectedly (PID $K3S_PID)"
        log "Last 50 lines of k3s log:"
        tail -50 /var/log/k3s-init-process.log | while read line; do
            log "  k3s: $line"
        done
        return 1
    fi
    return 0
}

# Wait for k3s to be ready
log "Waiting for k3s API to be ready..."
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
API_READY=false
for i in {1..60}; do
    if [ -f /etc/rancher/k3s/k3s.yaml ]; then
        if kubectl get nodes >/dev/null 2>&1; then
            log "k3s API is ready"
            API_READY=true
            break
        fi
    fi
    
    # Check if process is still alive
    if ! check_k3s_alive; then
        exit 1
    fi
    
    # Send watchdog keepalive
    systemd-notify WATCHDOG=1 || true
    
    if [ $((i % 10)) -eq 0 ]; then
        log "Still waiting for k3s API... ($i/60)"
    fi
    
    sleep 2
done

if [ "$API_READY" != "true" ]; then
    log "ERROR: k3s API not ready after 60 attempts"
    check_k3s_alive
    exit 1
fi

# Give k3s some time since it may restart for certificates etc.
sleep 15 

# Wait for current node to be ready
log "Waiting for node $HOSTNAME to be ready..."
NODE_READY=false
for i in {1..60}; do
    # Check process first
    if ! check_k3s_alive; then
        exit 1
    fi
    
    # Try to get node status
    if NODE_OUTPUT=$(kubectl get node "$HOSTNAME" -o json 2>&1); then
        NODE_STATUS=$(echo "$NODE_OUTPUT" | jq -r '.status.conditions[] | select(.type=="Ready") | .status' 2>/dev/null || echo "Unknown")
        if [ "$NODE_STATUS" = "True" ]; then
            log "Node $HOSTNAME is ready"
            NODE_READY=true
            break
        else
            log "Node status: $NODE_STATUS"
        fi
    else
        log "Failed to get node: $NODE_OUTPUT"
    fi
    
    # Send watchdog keepalive
    systemd-notify WATCHDOG=1 || true
    
    if [ $((i % 10)) -eq 0 ]; then
        log "Still waiting for node to be ready... ($i/60)"
    fi
    
    sleep 2
done

if [ "$NODE_READY" != "true" ]; then
    log "WARNING: Node $HOSTNAME not ready after 60 attempts, continuing anyway"
fi

# Check k3s is still alive
if ! check_k3s_alive; then
    exit 1
fi

# Find any nodes that don't match current hostname
log "Checking for old nodes..."
OLD_NODES=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' 2>/dev/null | tr ' ' '\n' | grep -v "^${HOSTNAME}$" || true)

if [ -n "$OLD_NODES" ]; then
    log "Found old node(s) to clean up: $OLD_NODES"
    
    for OLD_NODE in $OLD_NODES; do
        log "Draining old node: $OLD_NODE..."
        
        # Cordon first to prevent new pods
        kubectl cordon "$OLD_NODE" || true
        
        # Send watchdog keepalive
        systemd-notify WATCHDOG=1 || true
        
        # Drain the node - this will evict all pods
        kubectl drain "$OLD_NODE" \
            --ignore-daemonsets \
            --delete-emptydir-data \
            --force \
            --grace-period=30 \
            --timeout=60s || true
        
        # Delete the node
        kubectl delete node "$OLD_NODE" || log "Failed to delete node $OLD_NODE"
        log "Removed old node: $OLD_NODE"
        
        # Send watchdog keepalive
        systemd-notify WATCHDOG=1 || true
    done
else
    log "No old nodes found"
fi

# Display certificate information for verification
log "Verifying k3s server certificate SANs..."
if [ -f /var/lib/rancher/k3s/server/tls/serving-kube-apiserver.crt ]; then
    log "k3s API server certificate SANs:"
    openssl x509 -in /var/lib/rancher/k3s/server/tls/serving-kube-apiserver.crt -text -noout | grep -A 20 "Subject Alternative Name" | head -20 || log "Could not display certificate SANs"
else
    log "k3s server certificate not found (may not be created yet)"
fi

# Stop the k3s process
log "Stopping k3s process..."
if kill -0 $K3S_PID 2>/dev/null; then
    kill $K3S_PID
    wait $K3S_PID 2>/dev/null || true
    log "k3s process stopped"
else
    log "k3s process already stopped"
fi

# Wait a moment for clean shutdown
sleep 5

# Clean up temp log
rm /var/log/k3s-init-process.log

# Create marker file
mkdir -p /var/lib/rancher/k3s
touch /var/lib/rancher/k3s/.initialized

# Final network configuration summary
log "=== Network Configuration Summary ==="
log "Hostname: $HOSTNAME"
log "Local IP: $NODE_IP"
if [[ -n "$PUBLIC_IP" ]]; then
    log "Public IP: $PUBLIC_IP"
    log "External IP: $EXTERNAL_IP"
    log "Advertise Address: $ADVERTISE_IP"
    log "Certificates include both local and public IPs"
else
    log "Public IP: Not detected"
    log "External IP: $EXTERNAL_IP (same as local)"
    log "Advertise Address: $ADVERTISE_IP"
    log "Certificates include only local IP"
fi
log "TLS SANs: ${TLS_SANS[*]}"
log "======================================="

notify_systemd "Initialization complete"
log "k3s initialization complete - ready for k3s.service to start"