#!/bin/bash
# Sets the hostname environment variable for the attestation service

set -euo pipefail

# Configuration
ENV_CONFIG_FILE="/etc/attestation-service/attestation.env"

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2
}

log "Setting hostname for attestation service"

hostname=$(hostname)

# Check if HOSTNAME is already set in the config file
if [ -f "$ENV_CONFIG_FILE" ] && grep -q "^HOSTNAME=" "$ENV_CONFIG_FILE"; then
    current_hostname=$(grep "^HOSTNAME=" "$ENV_CONFIG_FILE" | cut -d'=' -f2)
    log "HOSTNAME already set to: $current_hostname"
    
    # # Optionally update if different
    # if [ "$current_hostname" != "$hostname" ]; then
    #     log "Current hostname ($hostname) differs from config ($current_hostname). Updating..."
    #     # Use sed to replace the existing HOSTNAME line
    #     sed -i "s/^HOSTNAME=.*/HOSTNAME=$hostname/" "$ENV_CONFIG_FILE"
    #     log "Updated hostname in config to: $hostname"
    # else
    #     log "Hostname matches current setting. No update needed."
    # fi
else
    # File doesn't exist or HOSTNAME not set, so add it
    echo "HOSTNAME=$hostname" >> "$ENV_CONFIG_FILE"
    log "Set hostname for attestation service to $hostname"
fi