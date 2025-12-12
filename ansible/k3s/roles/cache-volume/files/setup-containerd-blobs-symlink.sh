#!/bin/bash
# setup-containerd-blobs-symlink.sh - Create symlink for containerd blobs to cache volume
#
# This script creates a symlink so containerd writes image blobs directly to the
# cache volume. This allows blobs to persist across VM restarts and be shared.

set -euo pipefail

LOG_TAG="setup-containerd-blobs-symlink"

log() {
    echo "$1"
    logger -t "$LOG_TAG" "$1" 2>/dev/null || true
}

CACHE_BLOBS="/var/snap/containerd-blobs"
CONTAINERD_CONTENT="/var/lib/rancher/k3s/agent/containerd/io.containerd.content.v1.content"
BLOBS_PATH="$CONTAINERD_CONTENT/blobs"

# Ensure cache volume is mounted
if ! mountpoint -q /var/snap; then
    log "ERROR: /var/snap is not mounted, cannot create symlink"
    exit 1
fi

# Ensure cache blobs directory exists
mkdir -p "$CACHE_BLOBS/sha256"

# Ensure parent directory exists
mkdir -p "$CONTAINERD_CONTENT"

# If blobs path already exists
if [ -e "$BLOBS_PATH" ] || [ -L "$BLOBS_PATH" ]; then
    # If it's already a symlink pointing to the right place, we're done
    if [ -L "$BLOBS_PATH" ]; then
        CURRENT_TARGET=$(readlink "$BLOBS_PATH")
        if [ "$CURRENT_TARGET" = "$CACHE_BLOBS" ]; then
            log "Symlink already correctly configured"
            exit 0
        else
            log "Symlink points to wrong target ($CURRENT_TARGET), fixing..."
            rm "$BLOBS_PATH"
        fi
    else
        # It's a directory - migrate any existing blobs then remove it
        if [ -d "$BLOBS_PATH/sha256" ]; then
            BLOB_COUNT=$(find "$BLOBS_PATH/sha256" -type f 2>/dev/null | wc -l)
            if [ "$BLOB_COUNT" -gt 0 ]; then
                log "Migrating $BLOB_COUNT existing blobs to cache volume..."
                cp -an "$BLOBS_PATH/sha256"/* "$CACHE_BLOBS/sha256/" 2>/dev/null || true
            fi
        fi
        log "Removing existing blobs directory..."
        rm -rf "$BLOBS_PATH"
    fi
fi

# Create the symlink
ln -s "$CACHE_BLOBS" "$BLOBS_PATH"
log "Created symlink: $BLOBS_PATH -> $CACHE_BLOBS"

exit 0
