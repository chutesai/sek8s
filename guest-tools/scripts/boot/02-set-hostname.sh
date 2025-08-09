
#!/bin/bash
set -e

# Log function
log() {
    echo "$1" >> /var/log/first-boot-set-hostname.log
}

# Configuration
USER_DATA_FILE="/var/lib/cloud/instance/user-data.txt"

# Get node IP for fallback hostname
log "Determining node IP..."
NODE_IP=$(ip -4 addr show scope global | grep inet | awk '{print $2}' | cut -d'/' -f1 | head -n 1)
if [ -z "$NODE_IP" ]; then
    log "Failed to determine node IP, falling back to localhost"
    NODE_IP="127.0.0.1"
fi
log "Node IP set to $NODE_IP"

# Determine hostname
log "Determining hostname..."
# Check cloud-init user-data
if [ -f "$USER_DATA_FILE" ]; then
    NEW_HOSTNAME=$(grep '^hostname:' "$USER_DATA_FILE" | sed 's/hostname: *//' | tr -d '\n' | tr -d '[:space:]')
    if [ -n "$NEW_HOSTNAME" ]; then
        log "Using hostname from cloud-init user-data: $NEW_HOSTNAME"
    else
        log "'hostname' not set in $USER_DATA_FILE, can not set hostname..."
        exit 1
    fi
else
    log "$USER_DATA_FILE does not exist, can not set hostname..."
    exit 1
fi

# Validate hostname (alphanumeric, hyphens, 1-63 characters)
if [[ ! "$NEW_HOSTNAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9-]{0,61}[a-zA-Z0-9]$ ]]; then
    log "Error: Invalid hostname '$NEW_HOSTNAME', using random fallback"
    NEW_HOSTNAME="$FALLBACK_PREFIX-$(head /dev/urandom | tr -dc a-z0-9 | head -c 8)"
    log "Generated random hostname: $NEW_HOSTNAME"
fi

# Check current hostname
CURRENT_HOSTNAME=$(hostname)
if [ "$CURRENT_HOSTNAME" = "$NEW_HOSTNAME" ]; then
    log "Hostname already set to $NEW_HOSTNAME, skipping"
    exit 0
fi

# Set hostname
log "Setting hostname to $NEW_HOSTNAME..."
hostnamectl set-hostname "$NEW_HOSTNAME"
echo "$NEW_HOSTNAME" > /etc/hostname
log "Updated /etc/hostname with $NEW_HOSTNAME"

# Ensure preserve_hostname is set in cloud.cfg
CLOUD_CFG="/etc/cloud/cloud.cfg"
log "Configuring preserve_hostname in $CLOUD_CFG..."
if [ -f "$CLOUD_CFG" ]; then
    if grep -q "^preserve_hostname:" "$CLOUD_CFG"; then
        sed -i "s/^preserve_hostname:.*/preserve_hostname: true/" "$CLOUD_CFG"
        log "Updated preserve_hostname to true in $CLOUD_CFG"
    else
        echo "preserve_hostname: true" >> "$CLOUD_CFG"
        log "Appended preserve_hostname: true to $CLOUD_CFG"
    fi
else
    mkdir -p /etc/cloud
    echo "preserve_hostname: true" > "$CLOUD_CFG"
    log "Created $CLOUD_CFG with preserve_hostname: true"
fi
chmod 0644 "$CLOUD_CFG"

log "Hostname setup completed: $NEW_HOSTNAME"