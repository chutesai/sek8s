#!/usr/bin/env bash
# Mask nvidia-fabricmanager when no NVSwitch PCI devices are present.
# Fabric Manager is only needed for NVSwitch/NVLink fabric; without it,
# the service would error. Detection uses lspci (NVSwitch PCI device visible
# even when /dev/nvidia-nvswitch* nodes are not created).

set -euo pipefail

LOG_TAG="nvidia-fabricmanager-mask"

log() {
    local msg="$1"
    echo "[${LOG_TAG}] ${msg}"
    logger -t "${LOG_TAG}" "${msg}" >/dev/null 2>&1 || true
}

# Detect NVSwitch via lspci (e.g. "Bridge [0680]: ... H100 NVSwitch [10de:22a3]").
have_nvswitch() {
    lspci -nn 2>/dev/null | grep -i nvidia | grep -qi nvswitch
}

if have_nvswitch; then
    exit 0
fi

log "No NVSwitch PCI devices detected (lspci); stopping and masking nvidia-fabricmanager"
systemctl stop nvidia-fabricmanager || true
systemctl mask --runtime nvidia-fabricmanager || true
exit 0
