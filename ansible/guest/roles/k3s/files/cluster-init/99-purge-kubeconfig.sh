#!/bin/bash
# 99-purge-kubeconfig.sh — runs last in every post-start sequence.
#
# Purges every cluster-admin credential k3s leaves on the storage volume.  All post-start
# scripts that need kubectl/helm have already completed by the time this runs (alphabetical
# order places it last), so nothing that outlives init depends on them — k3s regenerates all
# of them on the next server start.
#
# After deletion, each path is measured into RTMR3:
#   - Absent (expected): no RTMR3 extension — baseline is unchanged.
#   - Present (deletion failed): SHA-384 of the file is extended into RTMR3.
#     The resulting value differs from the expected baseline and attestation
#     verifiers will reject the boot.
#
# This gives a second, post-init attestation checkpoint to complement the
# initramfs purge performed in setup_storage.  Together they certify that the
# kubeconfig was absent both before post-start started and after it finished.
set -euo pipefail

LOG_FILE="/var/log/k3s-post-start.log"
# The kubeconfig is only the entry point; the cert it embeds and the second admin kubeconfig
# under server/cred are equally usable credentials and were being left behind.
#
# The CA keys (client-ca, server-ca, request-header-ca, service) are deliberately NOT purged:
# miner kubeconfigs are signed by client-ca via the CSR API and the webhooks chain to it, so
# both break across reboots if it is rotated. That leaves the real exposure unchanged -- anyone
# with offline access to the storage volume can still mint an admin cert from client-ca.key.
# This removes a ready-to-use credential; it is not a boundary.
PURGE_PATHS="
/etc/rancher/k3s/k3s.yaml
/var/lib/rancher/k3s/server/cred/admin.kubeconfig
/var/lib/rancher/k3s/server/tls/client-admin.crt
/var/lib/rancher/k3s/server/tls/client-admin.key
"
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

# Delete each admin credential.  k3s regenerates them on the next server start so this does
# not affect runtime behaviour for any service that outlives init.
for path in $PURGE_PATHS; do
    if [ -f "$path" ]; then
        rm -f "$path"
        log "Deleted $path"
    else
        log "Already absent: $path"
    fi
done

# Measure post-deletion.  Absent: no-op, RTMR3 unchanged (expected baseline).
# Present: extend RTMR3 with the file hash so attestation detects the anomaly.
remaining=0
for path in $PURGE_PATHS; do
    [ -f "$path" ] || continue
    remaining=1
    log "ERROR: $path still present after deletion attempt"
    path_hash=$(sha384sum "$path" | awk '{print $1}')
    # `if !` rather than a separate $? test: under `set -e` the bare call would abort the
    # script before the check, making this poweroff unreachable.
    if ! "$TDX_RTMR_EXTEND" --index 3 --data "$path_hash" 2>/dev/null; then
        log "FATAL: tdx-rtmr-extend failed — cannot record credential presence in RTMR3"
        echo "99-purge-kubeconfig: FATAL: tdx-rtmr-extend failed" > /dev/kmsg 2>/dev/null || true
        sleep 5
        systemctl poweroff --force
    fi
    log "RTMR3 extended with hash of $path — attestation will fail"
done

if [ "$remaining" -ne 0 ]; then
    exit 1
fi

log "All admin credentials absent — RTMR3 baseline unchanged"
exit 0
