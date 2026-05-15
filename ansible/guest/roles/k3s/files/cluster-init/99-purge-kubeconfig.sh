#!/bin/bash
# 99-purge-kubeconfig.sh — runs last in every cluster-init sequence.
#
# The k3s admin kubeconfig at /etc/rancher/k3s/k3s.yaml is bind-mounted from
# the storage volume (/cache/storage/rancher-config/k3s.yaml).  All cluster-init
# scripts that need kubectl/helm have already completed by the time this script
# runs (alphabetical order places it last).  Deleting the file here ensures it
# does not persist on the cold storage volume between VM sessions.
#
# After deletion, the file path is measured into RTMR3:
#   - Absent (expected): no RTMR3 extension — baseline is unchanged.
#   - Present (deletion failed): SHA-384 of the file is extended into RTMR3.
#     The resulting value differs from the expected baseline and attestation
#     verifiers will reject the boot.
#
# This gives a second, post-init attestation checkpoint to complement the
# initramfs purge performed in setup_storage.  Together they certify that the
# kubeconfig was absent both before cluster-init started and after it finished.
set -euo pipefail

LOG_FILE="/var/log/k3s-cluster-init.log"
KUBECONFIG_PATH="/etc/rancher/k3s/k3s.yaml"
TDX_RTMR_EXTEND="/usr/local/bin/tdx-rtmr-extend"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [99-purge-kubeconfig] $1" | tee -a "$LOG_FILE"
}

# tdx-rtmr-extend is required for the RTMR3 guarantee.  If it is absent the VM
# cannot provide the attestation property this script exists to enforce — power
# off rather than silently continuing without the security guarantee.
if [ ! -x "$TDX_RTMR_EXTEND" ]; then
    log "FATAL: $TDX_RTMR_EXTEND not found or not executable — cannot enforce RTMR3 guarantee"
    echo "99-purge-kubeconfig: FATAL: tdx-rtmr-extend missing" > /dev/kmsg 2>/dev/null || true
    sleep 5
    systemctl poweroff --force
    exit 1
fi

# Delete the admin kubeconfig.  k3s regenerates it on the next server start so
# this does not affect runtime behaviour for any service that outlives init.
if [ -f "$KUBECONFIG_PATH" ]; then
    rm -f "$KUBECONFIG_PATH"
    log "Admin kubeconfig deleted from storage volume"
else
    log "Admin kubeconfig already absent — nothing to delete"
fi

# Measure the path post-deletion.
# If absent: this is a no-op and RTMR3 is unchanged (expected baseline).
# If present: extend RTMR3 with the file hash so attestation detects the anomaly.
if [ -f "$KUBECONFIG_PATH" ]; then
    log "ERROR: admin kubeconfig still present after deletion attempt"
    kubeconfig_hash=$(sha384sum "$KUBECONFIG_PATH" | awk '{print $1}')
    "$TDX_RTMR_EXTEND" --index 3 --data "$kubeconfig_hash" 2>/dev/null
    if [ $? -ne 0 ]; then
        log "FATAL: tdx-rtmr-extend failed — cannot record kubeconfig presence in RTMR3"
        echo "99-purge-kubeconfig: FATAL: tdx-rtmr-extend failed" > /dev/kmsg 2>/dev/null || true
        sleep 5
        systemctl poweroff --force
    fi
    log "RTMR3 extended with kubeconfig hash — attestation will fail"
    exit 1
fi

log "Admin kubeconfig absent — RTMR3 baseline unchanged"
exit 0
