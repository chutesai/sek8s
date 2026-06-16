#!/bin/bash
# /usr/local/bin/k3s-init-scripts/98-clear-terminal-pods.sh
# Clear terminal-phase pod tombstones (Failed/Succeeded) left in the API after a
# reboot — crash/Error leftovers, graceful-shutdown tombstones, and Deployment pods
# that exited and were replaced (e.g. the chutes agent).
#
# Only pods owned by a workload controller that does NOT retain terminal pods itself
# are deleted: ReplicaSet (Deployment), DaemonSet, StatefulSet. Their controllers
# already created replacements, so the terminal object is pure tombstone.
#
# This deliberately KEEPS:
#   - Job/CronJob pods — retention is governed by the Job's own
#     successful/failedJobsHistoryLimit (e.g. failed-chute-cleanup).
#   - operator-created one-shot pods not owned by the above controllers, e.g. the
#     gpu-operator validators (nvidia-cuda-validator).
#   - bare pods with no controller owner.
#
# Runs every boot (no run-once marker), ordered just before 99-purge-kubeconfig.sh
# so the admin kubeconfig is still present. terminated-pod-gc-threshold only caps
# growth between boots and won't reap a sub-threshold handful, so this is what gives
# a clean slate on each boot.
#
# Best-effort by design: this script MUST exit 0. The post-start runner powers off
# the VM if ANY post-start script exits non-zero, so every step is guarded and we
# never propagate a failure. (Intentionally no `set -e`.)

LOG_FILE="/var/log/k3s-post-start.log"
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "Clearing terminal-phase pod tombstones left over from previous boot..."

# If the API isn't reachable, skip quietly — never fail the boot for cosmetic cleanup.
if ! kubectl get --raw='/readyz' >/dev/null 2>&1; then
    log "API server not reachable — skipping pod cleanup"
    exit 0
fi

# Select terminal-phase pods whose CONTROLLER owner is ReplicaSet/DaemonSet/StatefulSet.
# field-selector can't match ownerReferences, so filter with jq.
targets=$(kubectl get pods --all-namespaces -o json 2>/dev/null | jq -r '
    .items[]
    | select(.status.phase == "Failed" or .status.phase == "Succeeded")
    | select((.metadata.ownerReferences // [])
             | any(.controller == true
                   and (.kind == "ReplicaSet" or .kind == "DaemonSet" or .kind == "StatefulSet")))
    | "\(.metadata.namespace) \(.metadata.name) \(.status.phase)"' 2>/dev/null)

if [ -z "$targets" ]; then
    log "No workload-controller tombstones to clear"
    exit 0
fi

log "Deleting workload-controller tombstone pod(s):"
while read -r ns name phase; do
    [ -n "$ns" ] && [ -n "$name" ] || continue
    log "  ${ns}/${name} (${phase})"
    kubectl delete pod "$name" -n "$ns" --ignore-not-found 2>&1 | tee -a "$LOG_FILE" || true
done <<< "$targets"

log "Pod tombstone cleanup complete"
exit 0
