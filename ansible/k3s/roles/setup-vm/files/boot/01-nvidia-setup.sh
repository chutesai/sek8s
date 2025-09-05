#!/bin/bash
set -e

# Log function
log() {
    echo "$1" >> /var/log/first-boot-nvidia.log
}

# Set NVIDIA device permissions
log "Setting NVIDIA device permissions..."
for device in /dev/nvidia* /dev/nvidiactl /dev/nvidia-uvm /dev/nvidia-uvm-tools; do
    if [ -e "$device" ]; then
        chmod 0666 "$device" && log "Set permissions to 0666 for $device"
    else
        log "Device $device not found, skipping"
    fi
done

# Create NVIDIA character device symlinks
log "Creating NVIDIA character device symlinks..."
for i in /dev/nvidia[0-9]; do
    if [ -e "$i" ]; then
        N=$(basename "$i" | sed 's/nvidia//')
        MAJ=$(ls -l "$i" | awk '{print $5}' | cut -d, -f1)
        MIN=$(ls -l "$i" | awk '{print $6}')
        mkdir -p "/dev/char/$MAJ:$MIN"
        ln -sf "$i" "/dev/char/$MAJ:$MIN" && log "Created symlink /dev/char/$MAJ:$MIN -> $i"
    else
        log "No NVIDIA devices found, skipping symlink creation"
    fi
done

log "NVIDIA setup completed."