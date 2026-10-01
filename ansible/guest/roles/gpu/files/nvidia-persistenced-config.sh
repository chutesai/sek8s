#!/usr/bin/env bash
# Select nvidia-persistenced's persistence flag from the GPU fabric topology.
#
# Writes ONE file: the NVPD_FLAG env file under /run. The systemd drop-in that consumes
# it is static and baked into the image (files/nvidia-persistenced-dropin.conf), because
# /etc/systemd/system is RTMR3-measured. Generating the drop-in here -- as this script
# did through 1.4.0 -- put a file in the measured tree that the build manifest did not
# contain, which moved RTMR3 on every boot after the first and failed attestation
# fleet-wide. /run is tmpfs and outside the measured set, so a per-host value can vary
# there without touching the measurement.
#
# Do not write anywhere under a path listed in tdx-measure-{gpu,miner}.conf from this or
# any other boot-time script.
set -euo pipefail

LOG_TAG="nvidia-persistenced-config"
# Overridable for tests only; production uses the /run path the static drop-in reads.
ENV_FILE="${NVPD_ENV_FILE:-/run/nvidia-persistenced-mode.env}"
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

mkdir -p "$(dirname "${ENV_FILE}")"

# Staged in the target directory and renamed, so nvidia-persistenced can never read a
# half-written env file: the rename is atomic and same-filesystem.
TMP_FILE=$(mktemp "${ENV_FILE}.XXXXXX")
trap 'rm -f "${TMP_FILE}"' EXIT
printf 'NVPD_FLAG=%s\n' "${FLAG}" > "${TMP_FILE}"
chmod 0644 "${TMP_FILE}"
mv -f "${TMP_FILE}" "${ENV_FILE}"

# No `systemctl daemon-reload` here: the drop-in is static, and an EnvironmentFile is
# read when the consuming unit starts. Before=nvidia-persistenced.service orders this
# write ahead of that start.
log "Wrote ${ENV_FILE} for ${MODE} mode"

exit 0
