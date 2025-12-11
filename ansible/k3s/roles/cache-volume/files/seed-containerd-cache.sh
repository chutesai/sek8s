#!/bin/bash
# seed-containerd-cache.sh - Copy preloaded containerd data into the cache volume

set -euo pipefail

SRC="/var/lib/rancher/k3s/agent/containerd"
CACHE_ROOT="/var/snap"
DST="$CACHE_ROOT/containerd"
LOG_TAG="seed-containerd-cache"

log() {
    local msg="$1"
    echo "$msg"
    logger -t "$LOG_TAG" "$msg" >/dev/null 2>&1 || true
}

SRC_DEV=""
DST_DEV=""

if command -v findmnt >/dev/null 2>&1; then
    SRC_DEV=$(findmnt -n -o SOURCE --target "$SRC" || true)
    DST_DEV=$(findmnt -n -o SOURCE --target "$CACHE_ROOT" || true)
fi

# Bail out if containerd is already bind-mounted; seeding must happen before k3s starts
if mountpoint -q "$SRC" && [ -n "$DST_DEV" ] && [ "$SRC_DEV" = "$DST_DEV" ]; then
    log "Containerd already bind-mounted; skipping seeding"
    exit 0
fi

# Ensure destination root is actually backed by the cache volume
if ! mountpoint -q "$CACHE_ROOT"; then
    log "Destination $CACHE_ROOT is not a mountpoint; refusing to seed"
    exit 1
fi

CACHE_DEVICE=$(findmnt -n -o SOURCE --target "$CACHE_ROOT" 2>/dev/null || true)
if command -v blkid >/dev/null 2>&1 && [ -n "$CACHE_DEVICE" ]; then
    CACHE_LABEL=$(blkid -s LABEL -o value "$CACHE_DEVICE" 2>/dev/null || true)
    if [ -n "$CACHE_LABEL" ] && [ "$CACHE_LABEL" != "tdx-cache" ]; then
        log "Destination label '$CACHE_LABEL' does not match expected 'tdx-cache'"
        exit 1
    fi
fi

if [ ! -d "$SRC" ]; then
    log "Source directory $SRC is missing; nothing to seed"
    exit 0
fi

mkdir -p "$DST"

# Marker is tied to the cache device UUID to avoid false positives when users swap volumes
MARKER="$DST/.seeded"
if command -v blkid >/dev/null 2>&1 && [ -n "$CACHE_DEVICE" ]; then
    DEVICE_UUID=$(blkid -s UUID -o value "$CACHE_DEVICE" 2>/dev/null || true)
    if [ -n "$DEVICE_UUID" ]; then
        MARKER="$DST/.seeded-${DEVICE_UUID}"
    fi
fi

if [ -f "$MARKER" ]; then
    log "Cache already seeded (marker $MARKER present)"
    exit 0
fi

# If destination already has content (beyond lost+found) assume it was previously seeded, but still clean runtime dirs.
DEST_HAS_CONTENT=false
if find "$DST" -mindepth 1 -maxdepth 1 -not -name 'lost+found' | read -r _; then
    DEST_HAS_CONTENT=true
fi

if [ "$DEST_HAS_CONTENT" != "true" ]; then
    # If the source is empty (should not happen on released images), just create the marker
    if ! find "$SRC" -mindepth 1 -maxdepth 1 | read -r _; then
        log "Source $SRC is empty; marking cache as seeded"
        touch "$MARKER"
        exit 0
    fi

    log "Seeding containerd cache from $SRC to $DST ..."
    if command -v rsync >/dev/null 2>&1; then
        rsync -aHAX --numeric-ids --delete "$SRC"/ "$DST"/
    else
        tar -C "$SRC" -cf - . | tar -C "$DST" -xf -
    fi
else
    log "Destination already populated; skipping data copy"
fi

# Remove runtime-specific state that must be recreated on each boot
for path in \
    "$DST/io.containerd.runtime.v2.task" \
    "$DST/io.containerd.grpc.v1.cri/sandboxes" \
    "$DST/io.containerd.sandbox.controller.v1.shim" \
    "$DST/tmpmounts"; do
    if [ -e "$path" ]; then
        rm -rf "$path"
    fi
done

find "$DST" -maxdepth 1 -type f -name 'containerd*.log*' -exec rm -f {} +

sync

touch "$MARKER"
log "Containerd cache seeding complete"
