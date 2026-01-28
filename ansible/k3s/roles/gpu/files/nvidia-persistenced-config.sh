#!/usr/bin/env bash
set -euo pipefail

LOG_TAG="nvidia-persistenced-config"
DROPIN_DIR="/etc/systemd/system/nvidia-persistenced.service.d"
DROPIN_FILE="${DROPIN_DIR}/override.conf"
MODE="persistence"
REASON="Defaulting to persistence mode"
DETECTION_SOURCE=""

log() {
    local msg="$1"
    echo "[${LOG_TAG}] ${msg}"
    logger -t "${LOG_TAG}" "${msg}" >/dev/null 2>&1 || true
}

# Detect NVSwitch via lspci (PCI device visible even when /dev/nvidia-nvswitch* nodes
# are not created). Matches guest lspci output e.g. "Bridge [0680]: ... H100 NVSwitch [10de:22a3]".
have_nvswitch() {
    if lspci -nn 2>/dev/null | grep -i nvidia | grep -qi nvswitch; then
        DETECTION_SOURCE="lspci"
        return 0
    fi
    return 1
}

if have_nvswitch; then
    MODE="uvm"
    if [[ -n "${DETECTION_SOURCE}" ]]; then
        REASON="NVSwitch fabric detected via ${DETECTION_SOURCE}"
    else
        REASON="NVSwitch fabric detected"
    fi
else
    MODE="persistence"
    REASON="No NVSwitch PCI devices detected (lspci); using standard persistence mode"
fi

if [[ "${MODE}" == "uvm" ]]; then
    FLAG="--uvm-persistence-mode"
else
    FLAG="--persistence-mode"
fi

log "${REASON}"
log "Ensuring nvidia-persistenced uses ${FLAG}"

mkdir -p "${DROPIN_DIR}"

read -r -d '' DESIRED_CONTENT <<EOF || true
[Unit]
Requires=nvidia-persistenced-config.service
After=nvidia-persistenced-config.service

[Service]
ExecStart=
ExecStart=/usr/bin/nvidia-persistenced ${FLAG} --verbose
EOF

TMP_FILE=$(mktemp)
trap 'rm -f "${TMP_FILE}"' EXIT
printf "%s\n" "${DESIRED_CONTENT}" > "${TMP_FILE}"

NEED_RELOAD=0
if [[ ! -f "${DROPIN_FILE}" ]] || ! cmp -s "${TMP_FILE}" "${DROPIN_FILE}"; then
    install -m 0644 "${TMP_FILE}" "${DROPIN_FILE}"
    NEED_RELOAD=1
    log "Updated ${DROPIN_FILE} for ${MODE} mode"
else
    log "${DROPIN_FILE} already configured for ${MODE} mode"
fi

if [[ ${NEED_RELOAD} -eq 1 ]]; then
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload
        log "Reloaded systemd daemon"
    else
        log "systemctl not found; please reload systemd manually"
    fi
fi

exit 0
