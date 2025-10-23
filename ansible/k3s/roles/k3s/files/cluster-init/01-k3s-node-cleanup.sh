#!/bin/bash
# /usr/local/bin/k3s-node-cleanup.sh
# k3s-node-cleanup: Clean up old nodes after k3s is stable and running
set -e

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a /var/log/k3s-node-cleanup.log
}

# Function to notify systemd we're still alive
notify_systemd() {
    if [ -n "$NOTIFY_SOCKET" ]; then
        systemd-notify --status="$1" || true
    fi
}

# Enhanced function to wait for API server to be fully ready
wait_for_api_server() {
    local max_attempts=120  # Increased timeout since k3s should already be starting
    local attempt=1
    
    log "Waiting for k3s API server to be ready..."
    
    while [ $attempt -le $max_attempts ]; do
        # Check if k3s service is running
        if ! systemctl is-active --quiet k3s; then
            log "k3s service is not active, waiting..."
            sleep 5
            ((attempt += 5))
            continue
        fi
        
        # Check basic API connectivity
        if kubectl get --raw='/readyz' >/dev/null 2>&1; then
            log "API server readiness check passed"
            return 0
        fi
        
        # Send watchdog keepalive
        systemd-notify WATCHDOG=1 || true
        
        if [ $((attempt % 15)) -eq 0 ]; then
            log "Still waiting for API server readiness... ($attempt/$max_attempts)"
        fi
        
        sleep 2
        ((attempt++))
    done
    
    log "ERROR: API server not ready after $max_attempts attempts"
    return 1
}

# Enhanced function to safely delete a node with retries
safe_delete_node() {
    local node_name="$1"
    local max_attempts=5
    local attempt=1
    
    log "Attempting to delete node: $node_name"
    
    while [ $attempt -le $max_attempts ]; do
        # Check if k3s service is still running
        if ! systemctl is-active --quiet k3s; then
            log "ERROR: k3s service stopped during node deletion"
            return 1
        fi
        
        # Check if node still exists
        if ! kubectl get node "$node_name" >/dev/null 2>&1; then
            log "Node $node_name no longer exists (already deleted)"
            return 0
        fi
        
        log "Delete attempt $attempt/$max_attempts for node: $node_name"
        
        # Try to delete the node with timeout
        if timeout 30 kubectl delete node "$node_name" --wait=false 2>/dev/null; then
            log "Delete command issued successfully for node: $node_name"
            
            # Wait a bit and verify deletion
            sleep 5
            if ! kubectl get node "$node_name" >/dev/null 2>&1; then
                log "Node $node_name successfully deleted"
                return 0
            else
                log "Node $node_name still exists after delete command"
            fi
        else
            log "Delete command failed for node: $node_name (attempt $attempt)"
        fi
        
        # Send watchdog keepalive
        systemd-notify WATCHDOG=1 || true
        
        sleep 5
        ((attempt++))
    done
    
    log "WARNING: Failed to delete node $node_name after $max_attempts attempts"
    return 1
}

# Enhanced function to drain a node with better error handling
safe_drain_node() {
    local node_name="$1"
    
    log "Draining node: $node_name..."
    
    # Check if k3s service is running
    if ! systemctl is-active --quiet k3s; then
        log "ERROR: k3s service not running"
        return 1
    fi
    
    # Cordon first to prevent new pods
    kubectl cordon "$node_name" 2>/dev/null || log "Failed to cordon node $node_name"
    
    # Send watchdog keepalive
    systemd-notify WATCHDOG=1 || true
    
    # Try standard drain first with reasonable timeout
    if kubectl drain "$node_name" \
        --ignore-daemonsets \
        --delete-emptydir-data \
        --force \
        --grace-period=10 \
        --timeout=60s 2>/dev/null; then
        log "Standard drain successful for node: $node_name"
        return 0
    fi
    
    log "Standard drain failed, attempting with disable-eviction..."
    
    # Try with disable-eviction
    if kubectl drain "$node_name" \
        --ignore-daemonsets \
        --delete-emptydir-data \
        --force \
        --grace-period=5 \
        --timeout=60s \
        --disable-eviction 2>/dev/null; then
        log "Drain with disable-eviction successful for node: $node_name"
        return 0
    fi
    
    log "Both drain methods failed for node: $node_name, but continuing with deletion..."
    return 1
}

log "Starting k3s node cleanup..."

# Tell systemd we're starting
notify_systemd "Starting node cleanup"

HOSTNAME=$(hostname)

# Set up kubectl
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Wait for API server to be ready
if ! wait_for_api_server; then
    log "ERROR: API server failed to become ready"
    notify_systemd "ERROR: API server not ready"
    exit 1
fi

# Give k3s additional time to stabilize
log "Allowing k3s to stabilize before cleanup..."
sleep 30

# Wait for current node to be ready
log "Waiting for node $HOSTNAME to be ready..."
NODE_READY=false
for i in {1..60}; do
    # Check if k3s service is still running
    if ! systemctl is-active --quiet k3s; then
        log "ERROR: k3s service stopped"
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
    log "WARNING: Node $HOSTNAME not ready after 60 attempts, continuing with cleanup anyway"
fi

# Find any nodes that don't match current hostname
log "Checking for old nodes to clean up..."
OLD_NODES=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' 2>/dev/null | tr ' ' '\n' | grep -v "^${HOSTNAME}$" || true)

if [ -n "$OLD_NODES" ]; then
    log "Found old node(s) to clean up: $OLD_NODES"
    
    notify_systemd "Cleaning up old nodes"
    
    for OLD_NODE in $OLD_NODES; do
        log "Processing old node: $OLD_NODE..."
        
        # Attempt to drain the node (but don't fail if it doesn't work)
        safe_drain_node "$OLD_NODE" || log "Drain failed for $OLD_NODE, but continuing with deletion"
        
        # Send watchdog keepalive
        systemd-notify WATCHDOG=1 || true
        
        # Attempt to delete the node with retries
        if safe_delete_node "$OLD_NODE"; then
            log "Successfully removed old node: $OLD_NODE"
        else
            log "Failed to remove old node: $OLD_NODE (this may need manual cleanup)"
        fi
        
        # Send watchdog keepalive
        systemd-notify WATCHDOG=1 || true
    done
    
    log "Old node cleanup completed"
else
    log "No old nodes found - cleanup not needed"
fi

# Create cleanup completion marker
touch /var/lib/rancher/k3s/.cleanup-completed

notify_systemd "Node cleanup complete"
log "k3s node cleanup completed successfully"