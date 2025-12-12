#!/bin/bash
# setup-chutes-agent-symlink.sh - Create symlink for Chutes agent state to cache volume
set -euo pipefail

LOG_TAG="setup-chutes-agent-symlink"

log() {
    echo "$1"
    logger -t "$LOG_TAG" "$1" 2>/dev/null || true
}

CACHE_AGENT="/var/snap/chutes-agent"
AGENT_TARGET="/var/lib/chutes/agent"

# Ensure cache volume is mounted
if ! mountpoint -q /var/snap; then
    log "ERROR: /var/snap is not mounted, cannot create symlink"
    exit 1
fi

# Ensure cache agent directory exists
mkdir -p "$CACHE_AGENT"

# Ensure parent directory exists
mkdir -p "/var/lib/chutes"

# If agent path already exists
if [ -e "$AGENT_TARGET" ] || [ -L "$AGENT_TARGET" ]; then
    # If it's already a symlink pointing to the right place, we're done
    if [ -L "$AGENT_TARGET" ]; then
        CURRENT_TARGET=$(readlink "$AGENT_TARGET")
        if [ "$CURRENT_TARGET" = "$CACHE_AGENT" ]; then
            log "Symlink already correctly configured"
            exit 0
        else
            log "Symlink points to wrong target ($CURRENT_TARGET), fixing..."
            rm "$AGENT_TARGET"
        fi
    else
        # It's a directory - migrate any existing files then remove it
        if [ "$(ls -A $AGENT_TARGET 2>/dev/null)" ]; then
            log "Migrating existing agent files to cache volume..."
            cp -an "$AGENT_TARGET"/* "$CACHE_AGENT/" 2>/dev/null || true
        fi
        log "Removing existing agent directory..."
        rm -rf "$AGENT_TARGET"
    fi
fi

# Create the symlink
ln -s "$CACHE_AGENT" "$AGENT_TARGET"
log "Created symlink: $AGENT_TARGET -> $CACHE_AGENT"

exit 0
