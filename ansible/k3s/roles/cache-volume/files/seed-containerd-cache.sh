#!/bin/bash
# seed-containerd-cache.sh - Copy preloaded containerd data into the cache volume

set -euo pipefail

SRC="/var/lib/rancher/k3s/agent/containerd"
DST="/var/snap/containerd"
MARKER="$DST/.seeded"
LOG_TAG="seed-containerd-cache"

log() {
    local msg="$1"
    echo "$msg"
    logger -t "$LOG_TAG" "$msg" >/dev/null 2>&1 || true
}

if mountpoint -q "$SRC"; then
    log "Containerd already bind-mounted; skipping seeding"
    exit 0
fi

if [ -f "$MARKER" ]; then
    log "Cache already seeded (marker present)"
    exit 0
fi

if [ ! -d "$SRC" ]; then
    log "Source directory $SRC is missing; nothing to seed"
    exit 0
fi

if [ ! -d "$DST" ]; then
    log "Destination directory $DST is missing"
    exit 1
fi

# If destination already has more than the default lost+found directory, assume it was seeded
if find "$DST" -mindepth 1 -maxdepth 1 -not -name 'lost+found' | read -r _; then
    log "Destination already populated; creating marker and skipping copy"
    touch "$MARKER"
    exit 0
fi

# If the source is empty (should not happen on released images), just create the marker
if ! find "$SRC" -mindepth 1 -maxdepth 1 | read -r _; then
    log "Source $SRC is empty; marking cache as seeded"
    touch "$MARKER"
    exit 0
fi

log "Seeding containerd cache from $SRC to $DST ..."
if command -v rsync >/dev/null 2>&1; then
    rsync -aHAX --numeric-ids "$SRC"/ "$DST"/
else
    tar -C "$SRC" -cf - . | tar -C "$DST" -xf -
fi

sync

touch "$MARKER"
log "Containerd cache seeding complete"
