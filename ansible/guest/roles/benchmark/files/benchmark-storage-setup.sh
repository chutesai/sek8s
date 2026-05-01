#!/bin/bash
# benchmark-storage-setup.sh - Identify and prepare the storage block device for benchmark VMs.
#
# In benchmark mode the virtio device order is:
#   vda = boot disk    (qcow2 image)
#   vdb = config volume  (labeled tdx-config, mounted at /var/config)
#   vdc = storage volume  (labeled 'storage' when new, LUKS header after encryption)
#
# This script:
#   1. Locates the storage device (first virtio disk that is not boot and not the config volume)
#   2. Creates /dev/chutes-storage as a stable symlink to the identified device
#   3. Creates /data as the standard mount point
#   4. If the device has a plain filesystem, mounts it at /data automatically
#   5. If the device is LUKS encrypted, logs that luks-setup open is needed
#   6. If the device is unformatted, logs that luks-setup setup is needed

set -euo pipefail

LOG_TAG="benchmark-storage"
MOUNT_POINT="/data"
SYMLINK="/dev/chutes-storage"

log() { echo "$1"; logger -t "$LOG_TAG" "$1" 2>/dev/null || true; }

find_storage_device() {
    # The config volume carries the 'tdx-config' label; skip it.
    local config_dev
    config_dev=$(blkid -l -o device -t LABEL=tdx-config 2>/dev/null || true)

    for dev in /dev/vdb /dev/vdc /dev/vdd /dev/vde; do
        [[ -b "$dev" ]] || continue
        [[ "$dev" == "/dev/vda" ]] && continue
        if [[ -n "$config_dev" ]]; then
            [[ "$(readlink -f "$dev")" == "$(readlink -f "$config_dev")" ]] && continue
        fi
        echo "$dev"
        return 0
    done
    return 1
}

DEVICE=$(find_storage_device) || {
    log "ERROR: Could not find storage block device"
    exit 1
}
log "Storage device identified: $DEVICE"

# Create stable symlink so tools and documentation can reference a fixed path
ln -sf "$DEVICE" "$SYMLINK"
log "Symlink: $SYMLINK -> $DEVICE"

# Ensure the standard mount point exists
mkdir -p "$MOUNT_POINT"
log "Mount point: $MOUNT_POINT"

# Determine device state and act accordingly
if cryptsetup isLuks "$DEVICE" 2>/dev/null; then
    log "Storage is LUKS-encrypted."
    log "  Run:  luks-setup open   to unlock and mount at $MOUNT_POINT"
elif blkid -o value -s TYPE "$DEVICE" 2>/dev/null | grep -q .; then
    FS_TYPE=$(blkid -o value -s TYPE "$DEVICE")
    log "Storage has $FS_TYPE filesystem. Mounting at $MOUNT_POINT..."
    mount "$DEVICE" "$MOUNT_POINT"
    log "Mounted at $MOUNT_POINT"
else
    log "Storage is unformatted."
    log "  Run:  luks-setup setup   to encrypt and format"
fi

log "Benchmark storage setup complete."
exit 0
