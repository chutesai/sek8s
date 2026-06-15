#!/bin/bash
# /usr/local/bin/k3s-config-init.sh
# k3s-config-init: Generate k3s configuration before service starts.
# Runs every boot so new image versions can inject updated API server args
# (e.g. authorization webhook) without manual migration. The CA and existing
# certs on the storage volume are untouched; k3s only regenerates leaf certs
# when TLS SANs actually change.
set -e

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a /var/log/k3s-config-init.log
}

# Clear stale k3s/CNI runtime state left by an ungraceful stop or crash, before
# k3s starts. This runs in the existing pre-k3s hook (k3s Requires= this unit,
# which runs After= the storage bind mount), so no separate service is needed.
#
# After a force-kill (the shutdown `pkill containerd-shim` backstop) or a hard
# crash, leftover CNI IPAM/interfaces and kubelet pod mounts persist on the
# storage volume and block the kubelet from GC'ing stale pod sandboxes — leaving
# pods wedged with multiple containerd sandboxes (a split-brain the kubelet never
# converges). Clearing it here lets the kubelet start clean and reconcile.
#
# Best-effort: every step is guarded so it can never fail config generation
# (k3s Requires= this unit), and it never touches the containerd image content
# store (/var/lib/rancher/k3s/agent/containerd) — images are preserved.
cleanup_stale_runtime_state() {
    log "Clearing stale k3s/CNI runtime state before k3s start..."
    if [ -r /proc/self/mounts ]; then
        local mp
        while read -r mp; do
            [ -n "$mp" ] || continue
            umount "$mp" 2>/dev/null || umount -l "$mp" 2>/dev/null || true
        done < <(awk '$2 ~ "^/var/lib/kubelet/pods" || $2 ~ "^/var/lib/kubelet/plugins" || $2 ~ "^/run/k3s" || $2 ~ "^/run/netns/cni-" {print $2}' /proc/self/mounts | sort -r)
    fi
    rm -rf /var/lib/cni/networks /var/lib/cni/results 2>/dev/null || true
    local link
    for link in cni0 flannel.1 flannel-v6.1; do
        ip link delete "$link" 2>/dev/null || true
    done
    rm -rf /run/flannel 2>/dev/null || true
    log "Stale runtime state cleanup complete"
    return 0
}

# Run the cleanup first so k3s/kubelet start from a clean slate on every boot.
cleanup_stale_runtime_state || true

# Public IP detection configuration
INCLUDE_PUBLIC_IP="${INCLUDE_PUBLIC_IP:-true}"
PUBLIC_IP_TIMEOUT="${PUBLIC_IP_TIMEOUT:-5}"
USE_PUBLIC_IP_FOR_ADVERTISE="${USE_PUBLIC_IP_FOR_ADVERTISE:-false}"

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
            # Log to stderr to avoid contaminating the return value
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Detected public IP from $service: $public_ip" >&2
            echo "$public_ip"
            return 0
        fi
    done
    
    # Log to stderr to avoid contaminating the return value  
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Warning: Could not detect public IP address" >&2
    return 1
}

log "Starting k3s configuration generation..."

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
AUTHZ_WEBHOOK_CONFIG="/etc/admission-controller/authorization-webhook-config.yaml"
cat >> /etc/rancher/k3s/config.yaml << EOF
write-kubeconfig-mode: "0600"
disable:
  - traefik
  - servicelb
cluster-cidr: 10.42.0.0/16
service-cidr: 10.43.0.0/16
EOF

# Build kube-apiserver-arg list.  Both encryption and the authorization webhook
# are kube-apiserver flags; they must be passed via kube-apiserver-arg, not as
# top-level k3s config keys (unknown top-level keys are silently ignored by k3s).
ENCRYPTION_CONFIG="/run/chutes/k3s-encryption-config.yaml"

# Debug builds bake a static encryption config at /etc/chutes; production writes
# the real one to /run/chutes from initramfs before this script runs. Materialize
# the debug copy here — BEFORE the check below — so debug enables encryption at the
# same boot stage prod does. Otherwise this script (which runs before k3s.service)
# never sees the file, because the debug copy was previously done by k3s.service's
# ExecStartPre, which runs AFTER this — leaving encryption off for the whole boot.
DEBUG_ENCRYPTION_SRC="/etc/chutes/k3s-encryption-config.yaml"
if [ ! -f "$ENCRYPTION_CONFIG" ] && [ -f "$DEBUG_ENCRYPTION_SRC" ]; then
    mkdir -m 700 -p /run/chutes
    cp "$DEBUG_ENCRYPTION_SRC" "$ENCRYPTION_CONFIG"
    chmod 600 "$ENCRYPTION_CONFIG"
    log "Materialized debug secrets-encryption config from $DEBUG_ENCRYPTION_SRC"
fi

KUBE_API_ARGS=()

if [ -f "$ENCRYPTION_CONFIG" ]; then
    KUBE_API_ARGS+=("encryption-provider-config=${ENCRYPTION_CONFIG}")
    log "Secrets encryption enabled: $ENCRYPTION_CONFIG"
else
    log "WARNING: $ENCRYPTION_CONFIG not found — k3s will start without secrets encryption"
fi

if [ -f "$AUTHZ_WEBHOOK_CONFIG" ]; then
    KUBE_API_ARGS+=(
        "authorization-mode=Node,Webhook,RBAC"
        "authorization-webhook-config-file=${AUTHZ_WEBHOOK_CONFIG}"
        "authorization-webhook-version=v1"
        "authorization-webhook-cache-authorized-ttl=5m"
        "authorization-webhook-cache-unauthorized-ttl=2m"
    )
    log "Authorization webhook enabled: $AUTHZ_WEBHOOK_CONFIG"
else
    log "Authorization webhook config not found, using default authorization (Node,RBAC)"
fi

if [ ${#KUBE_API_ARGS[@]} -gt 0 ]; then
    echo "kube-apiserver-arg:" >> /etc/rancher/k3s/config.yaml
    for arg in "${KUBE_API_ARGS[@]}"; do
        echo "  - \"${arg}\"" >> /etc/rancher/k3s/config.yaml
    done
fi

# Kubelet graceful node shutdown. shutdownGracePeriod is a KubeletConfiguration
# field with no equivalent CLI flag, so it's dropped into a config-dir that k3s
# merges over its generated kubelet config. Written here (at runtime) because
# /etc/rancher/k3s is a storage bind mount that starts empty — an image-baked
# file under it would be shadowed. Pairs with the logind InhibitDelayMaxSec
# drop-in, which must be >= shutdownGracePeriod or pods get killed mid-drain.
KUBELET_CONF_DIR="/etc/rancher/k3s/kubelet.conf.d"
mkdir -p "$KUBELET_CONF_DIR"
cat > "$KUBELET_CONF_DIR/10-graceful-shutdown.conf" << 'KUBELET_EOF'
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
shutdownGracePeriod: 30s
shutdownGracePeriodCriticalPods: 10s
KUBELET_EOF
cat >> /etc/rancher/k3s/config.yaml << EOF
kubelet-arg:
  - "config-dir=${KUBELET_CONF_DIR}"
EOF
log "Kubelet graceful shutdown configured (config-dir=$KUBELET_CONF_DIR, grace 30s)"

# Log the configuration for debugging
log "k3s configuration created with the following settings:"
log "  node-name: $HOSTNAME"
log "  node-ip: $NODE_IP" 
log "  node-external-ip: $EXTERNAL_IP"
log "  advertise-address: $ADVERTISE_IP"
log "  TLS SANs: ${TLS_SANS[*]}"

# Final network configuration summary
log "=== Network Configuration Summary ==="
log "Hostname: $HOSTNAME"
log "Local IP: $NODE_IP"
if [[ -n "$PUBLIC_IP" ]]; then
    log "Public IP: $PUBLIC_IP"
    log "External IP: $EXTERNAL_IP"
    log "Advertise Address: $ADVERTISE_IP"
    log "Certificates will include both local and public IPs"
else
    log "Public IP: Not detected"
    log "External IP: $EXTERNAL_IP (same as local)"
    log "Advertise Address: $ADVERTISE_IP"
    log "Certificates will include only local IP"
fi
log "TLS SANs: ${TLS_SANS[*]}"
log "======================================="

log "k3s configuration generation complete - ready for k3s.service to start"