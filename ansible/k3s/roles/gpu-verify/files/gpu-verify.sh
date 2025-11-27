#!/usr/bin/env bash
set -euo pipefail

log() { echo "[gpu-verify] $*"; }
fatal() {
    echo "[gpu-verify] FATAL: $*"
    if [[ "${GPU_VERIFY_DEBUG_MODE:-false}" == "true" ]]; then
        echo "[gpu-verify] DEBUG MODE: would shutdown but continuing"
        return 0
    fi
    sleep 1
    /usr/sbin/shutdown -h now
    exit 1
}

log "Starting GPU verification..."

# Enumerate expected VFIO-exposed NVIDIA GPUs via PCI
mapfile -t expected_gpu_bdfs < <(
    for dev in /sys/bus/pci/devices/*; do
        [[ -f "$dev/vendor" ]] || continue
        vendor=$(cat "$dev/vendor")
        class=$(cat "$dev/class")
        if [[ "$vendor" == "0x10de" && ( "$class" == 0x0300* || "$class" == 0x0302* ) ]]; then
            basename "$dev"
        fi
    done | sort
)

EXPECTED_COUNT=${#expected_gpu_bdfs[@]}

log "Expected GPUs (VFIO topology): $EXPECTED_COUNT"
printf "  %s\n" "${expected_gpu_bdfs[@]}"

if [[ $EXPECTED_COUNT -eq 0 ]]; then
    fatal "No expected NVIDIA GPUs detected via PCI — passthrough failure?"
fi

# Enumerate visible GPUs via the NVIDIA driver
mapfile -t visible_gpu_bdfs < <(
    nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader | sed 's/^GPU-//g' | sort || true
)

VISIBLE_COUNT=${#visible_gpu_bdfs[@]}

log "Visible GPUs (nvidia-smi): $VISIBLE_COUNT"
printf "  %s\n" "${visible_gpu_bdfs[@]}"

if [[ $VISIBLE_COUNT -eq 0 ]]; then
    fatal "nvidia-smi shows 0 GPUs — driver load failure or bad passthrough"
fi

# Cross-check counts and identities
if [[ "$VISIBLE_COUNT" -ne "$EXPECTED_COUNT" ]]; then
    fatal "GPU count mismatch: expected $EXPECTED_COUNT but nvidia-smi sees $VISIBLE_COUNT"
fi

for gpu in "${expected_gpu_bdfs[@]}"; do
    if ! printf "%s\n" "${visible_gpu_bdfs[@]}" | grep -q "$gpu"; then
        fatal "Missing expected GPU $gpu — passthrough incomplete"
    fi
done

log "✓ All expected GPUs are visible in nvidia-smi."
exit 0
