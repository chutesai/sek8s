#!/bin/bash
# /usr/local/bin/k3s-node-cleanup.sh
# k3s-node-cleanup: Clean up old nodes after k3s is stable and running
# Assumes build nodes were pre-drained before image creation
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

# Wait for API server to be fully ready
wait_for_api_server() {
    local max_attempts=120
    local attempt=1
    
    log "Waiting for k3s API server to be ready..."
    
    while [ $attempt -le $max_attempts ]; do
        if ! systemctl is-active --quiet k3s; then
            log "k3s service is not active, waiting..."
            sleep 5
            attempt=$((attempt + 5))
            continue
        fi
        
        if kubectl get --raw='/readyz' >/dev/null 2>&1; then
            log "API server readiness check passed"
            return 0
        fi
        
        systemd-notify WATCHDOG=1 || true
        
        if [ $((attempt % 15)) -eq 0 ]; then
            log "Still waiting for API server readiness... ($attempt/$max_attempts)"
        fi
        
        sleep 2
        attempt=$((attempt + 1))
    done
    
    log "ERROR: API server not ready after $max_attempts attempts"
    return 1
}

# Force delete old build nodes (they were pre-drained, so no drain needed)
force_delete_old_node() {
    local node_name="$1"
    local max_attempts=5
    local attempt=1
    
    log "Deleting old build node: $node_name (pre-drained at build time)"
    
    while [ $attempt -le $max_attempts ]; do
        if ! kubectl get node "$node_name" >/dev/null 2>&1; then
            log "Node $node_name no longer exists"
            return 0
        fi
        
        log "Delete attempt $attempt/$max_attempts for node: $node_name"
        
        # Delete without waiting for graceful shutdown (it's already drained)
        if kubectl delete node "$node_name" --wait=false --grace-period=0 2>&1 | tee -a /var/log/k3s-node-cleanup.log; then
            log "Delete command issued for node: $node_name"
            
            sleep 3
            if ! kubectl get node "$node_name" >/dev/null 2>&1; then
                log "Node $node_name successfully deleted"
                return 0
            fi
        fi
        
        systemd-notify WATCHDOG=1 || true
        sleep 2
        attempt=$((attempt + 1))
    done
    
    log "WARNING: Failed to delete node $node_name after $max_attempts attempts"
    return 1
}

# Clean up any orphaned pods (shouldn't be many if build cleanup worked)
cleanup_orphaned_pods() {
    local node_name="$1"
    
    log "Checking for orphaned pods on node: $node_name"
    
    local orphaned_pods=$(kubectl get pods --all-namespaces -o json 2>/dev/null | \
        jq -r ".items[] | select(.spec.nodeName == \"$node_name\") | \"\(.metadata.namespace)/\(.metadata.name)\"" 2>/dev/null || true)
    
    if [ -n "$orphaned_pods" ]; then
        log "WARNING: Found orphaned pods (build cleanup may have failed), force deleting..."
        echo "$orphaned_pods" | while read pod_ref; do
            if [ -n "$pod_ref" ]; then
                local namespace=$(echo "$pod_ref" | cut -d'/' -f1)
                local pod_name=$(echo "$pod_ref" | cut -d'/' -f2)
                log "Force deleting pod: $namespace/$pod_name"
                kubectl delete pod "$pod_name" -n "$namespace" --grace-period=0 --force 2>&1 | tee -a /var/log/k3s-node-cleanup.log || true
            fi
        done
        log "Orphaned pod cleanup completed"
    else
        log "No orphaned pods found (as expected with build-time cleanup)"
    fi
}

log "Starting k3s node cleanup..."
notify_systemd "Starting node cleanup"

HOSTNAME=$(hostname)
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Wait for API server
if ! wait_for_api_server; then
    log "ERROR: API server failed to become ready"
    notify_systemd "ERROR: API server not ready"
    exit 1
fi

# Give k3s time to stabilize
log "Allowing k3s to stabilize before cleanup..."
sleep 30

# Wait for current node to be ready
log "Waiting for node $HOSTNAME to be ready..."
for i in {1..60}; do
    if ! systemctl is-active --quiet k3s; then
        log "ERROR: k3s service stopped"
        exit 1
    fi
    
    if kubectl get node "$HOSTNAME" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q "True"; then
        log "Node $HOSTNAME is ready"
        break
    fi
    
    systemd-notify WATCHDOG=1 || true
    
    if [ $((i % 10)) -eq 0 ]; then
        log "Still waiting for node to be ready... ($i/60)"
    fi
    
    sleep 2
done

# Find old nodes (build nodes that were pre-drained)
log "Checking for old build nodes to clean up..."
OLD_NODES=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' 2>/dev/null | tr ' ' '\n' | grep -v "^${HOSTNAME}$" || true)

if [ -n "$OLD_NODES" ]; then
    log "Found old build node(s) to clean up: $OLD_NODES"
    notify_systemd "Cleaning up old build nodes"
    
    for OLD_NODE in $OLD_NODES; do
        log "Processing old build node: $OLD_NODE..."
        
        # Get node status
        NODE_STATUS=$(kubectl get node "$OLD_NODE" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "Unknown")
        log "Node $OLD_NODE status: $NODE_STATUS"
        
        # Delete the old node (it was pre-drained at build time)
        if force_delete_old_node "$OLD_NODE"; then
            log "Successfully removed old build node: $OLD_NODE"
        else
            log "WARNING: Failed to remove node: $OLD_NODE (may need manual cleanup)"
        fi
        
        systemd-notify WATCHDOG=1 || true
        
        # Clean up any orphaned pods (shouldn't be many)
        cleanup_orphaned_pods "$OLD_NODE"
        
        systemd-notify WATCHDOG=1 || true
    done
    
    log "Old build node cleanup completed"
else
    log "No old build nodes found - cleanup not needed"
fi

# Create completion marker
touch /var/lib/rancher/k3s/.cleanup-completed

notify_systemd "Node cleanup complete"
log "k3s node cleanup completed successfully"