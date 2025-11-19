#!/usr/bin/env bash
set -euo pipefail

log() { echo "[enable-ppcie] $*"; }
fatal() {
    echo "[enable-ppcie] FATAL: $*"
    # give logs time to flush
    sleep 1
    /usr/sbin/shutdown -h now
    exit 1
}

log "Starting GPU passthrough validation & PPCIe enable procedure..."

###############################################################################
# 1. ENUMERATE EXPECTED GPUs (PCI devices exposed by QEMU to the guest)
###############################################################################
mapfile -t expected_gpu_bdfs < <(
    for dev in /sys/bus/pci/devices/*; do
        [[ -f "$dev/vendor" ]] || continue
        vendor=$(cat "$dev/vendor")
        class=$(cat "$dev/class")
        if [[ "$vendor" == "0x10de" && "$class" == 0x0300* || "$class" == 0x0302* ]]; then
            basename "$dev"
        fi
    done | sort
)

EXPECTED_COUNT=${#expected_gpu_bdfs[@]}

log "Expected GPUs (QEMU VFIO topology): $EXPECTED_COUNT"
printf "  %s\n" "${expected_gpu_bdfs[@]}"

if [[ $EXPECTED_COUNT -eq 0 ]]; then
    fatal "No expected NVIDIA GPUs detected via PCI — passthrough failure?"
fi

###############################################################################
# 2. ENUMERATE ACTUAL NVIDIA GPUs (via driver)
###############################################################################
mapfile -t visible_gpu_bdfs < <(
    nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader | sed 's/^GPU-//g' | sort || true
)

VISIBLE_COUNT=${#visible_gpu_bdfs[@]}

log "Visible GPUs (nvidia-smi): $VISIBLE_COUNT"
printf "  %s\n" "${visible_gpu_bdfs[@]}"

if [[ $VISIBLE_COUNT -eq 0 ]]; then
    fatal "nvidia-smi shows 0 GPUs — driver load failure or bad passthrough"
fi

###############################################################################
# 3. CROSS-CHECK: All expected GPUs must be visible
###############################################################################
if [[ "$VISIBLE_COUNT" -ne "$EXPECTED_COUNT" ]]; then
    fatal "GPU count mismatch: expected $EXPECTED_COUNT but nvidia-smi sees $VISIBLE_COUNT"
fi

for gpu in "${expected_gpu_bdfs[@]}"; do
    if ! printf "%s\n" "${visible_gpu_bdfs[@]}" | grep -q "$gpu"; then
        fatal "Missing expected GPU $gpu — passthrough incomplete"
    fi
done

log "✓ All expected GPUs are visible in nvidia-smi."

###############################################################################
# 4. ENABLE PPCIe ReadyState=1
###############################################################################

log "Checking current PPCIe ReadyState..."

if nvidia-smi conf-compute -q | grep -q "CC GPUs Ready State *: *Ready"; then
    log "PPCIe is already enabled. Exiting."
    exit 0
fi

log "Enabling PPCIe ReadyState=1..."
if ! nvidia-smi conf-compute -srs 1; then
    fatal "Failed to issue PPCIe ReadyState command"
fi

# verify
if ! nvidia-smi conf-compute -q | grep -q "Ready"; then
    fatal "PPCIe ReadyState did not successfully transition to Ready"
fi

log "✓ PPCIe GPUs enabled successfully."
exit 0
