#!/usr/bin/env bash
# Mask nvidia-fabricmanager when no NVSwitch devices are present.
# Fabric Manager is only needed for NVSwitch/NVLink fabric; without it,
# the service would error. Run after nvidia-persistenced-config so device
# topology is known.

set -euo pipefail

LOG_TAG="nvidia-fabricmanager-mask"

log() {
    local msg="$1"
    echo "[${LOG_TAG}] ${msg}"
    logger -t "${LOG_TAG}" "${msg}" >/dev/null 2>&1 || true
}

have_nvswitch() {
    shopt -s nullglob
    local devices=(
        /dev/nvidia-nvswitch
        /dev/nvidia-nvswitch[0-9]*
        /dev/nvidia-nvlink
        /dev/nvidia-nvlink[0-9]*
    )
    for dev in "${devices[@]}"; do
        if [[ -e "${dev}" && "${dev}" != *ctl ]]; then
            shopt -u nullglob
            return 0
        fi
    done
    shopt -u nullglob
    return 1
}

if have_nvswitch; then
    log "NVSwitch/NVLink devices present; leaving nvidia-fabricmanager enabled"
    systemctl stop nvidia-fabricmanager || true
    systemctl unmask --runtime nvidia-fabricmanager 2>/dev/null || true
    exit 0
fi

log "No NVSwitch/NVLink device nodes detected; masking nvidia-fabricmanager"
systemctl stop nvidia-fabricmanager || true
systemctl mask --runtime nvidia-fabricmanager || true
exit 0
