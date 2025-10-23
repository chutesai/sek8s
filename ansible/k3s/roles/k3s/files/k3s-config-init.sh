#!/bin/bash
# /usr/local/bin/k3s-config-init.sh
# k3s-config-init: Generate k3s configuration before service starts
set -e

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a /var/log/k3s-config-init.log
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

# Tell systemd we're starting
notify_systemd "Generating k3s configuration"

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

notify_systemd "Configuration generation complete"
systemd-notify --ready
log "k3s configuration generation complete - ready for k3s.service to start"