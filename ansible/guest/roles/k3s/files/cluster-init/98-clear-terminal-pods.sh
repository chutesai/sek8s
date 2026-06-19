#!/bin/bash
# /usr/local/bin/k3s-init-scripts/98-clear-terminal-pods.sh
# Clear stale pod records left in the API after a reboot:
#   - terminal-phase tombstones (Failed/Succeeded) — crash/Error leftovers,
#     graceful-shutdown tombstones, and Deployment pods that exited and were
#     replaced (e.g. the chutes agent); and
#   - stuck pods from the previous boot the controller will not reconcile on its
#     own: phase==Unknown, and pods with status.reason==NodeLost (e.g. an
#     attestation-proxy DaemonSet pod orphaned by an ungraceful shutdown — the
#     DaemonSet will not replace a pod it still believes exists, so the proxy
#     stays down until the tombstone is removed). NodeLost should be rare on a
#     single-node miner, but Unknown/NodeLost are cleared as a self-heal.
#
# Only pods owned by a workload controller that does NOT retain terminal pods itself
# are deleted: ReplicaSet (Deployment), DaemonSet, StatefulSet. For Failed/Succeeded
# the controller already created a replacement (pure tombstone); for Unknown/NodeLost
# deleting the stale object is what lets the controller create the replacement.
#
# This deliberately KEEPS:
#   - Job/CronJob pods — retention is governed by the Job's own
#     successful/failedJobsHistoryLimit (e.g. failed-chute-cleanup).
#   - operator-created one-shot pods not owned by the above controllers, e.g. the
#     gpu-operator validators (nvidia-cuda-validator).
#   - bare pods with no controller owner.
#
# Failed/Succeeded are deleted gracefully. Unknown/NodeLost are force-deleted
# (--force --grace-period=0): the normal delete blocks on a kubelet/node that will
# never confirm, and because this runs once early each boot, any such pod is a
# leftover from a PREVIOUS boot whose containers are already gone — so force-delete
# cannot orphan a running container.
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

log "Clearing stale pod records left over from previous boot..."

# If the API isn't reachable, skip quietly — never fail the boot for cosmetic cleanup.
if ! kubectl get --raw='/readyz' >/dev/null 2>&1; then
    log "API server not reachable — skipping pod cleanup"
    exit 0
fi

# Select stale pods (terminal phase, Unknown, or NodeLost) whose CONTROLLER owner is
# ReplicaSet/DaemonSet/StatefulSet. field-selector can't match ownerReferences or
# status.reason, so filter with jq. The trailing reason field drives force-vs-graceful.
targets=$(kubectl get pods --all-namespaces -o json 2>/dev/null | jq -r '
    .items[]
    | select(.status.phase == "Failed" or .status.phase == "Succeeded"
             or .status.phase == "Unknown" or .status.reason == "NodeLost")
    | select((.metadata.ownerReferences // [])
             | any(.controller == true
                   and (.kind == "ReplicaSet" or .kind == "DaemonSet" or .kind == "StatefulSet")))
    | "\(.metadata.namespace) \(.metadata.name) \(.status.phase) \(.status.reason // "-")"' 2>/dev/null)

if [ -z "$targets" ]; then
    log "No workload-controller pod records to clear"
    exit 0
fi

log "Deleting stale workload-controller pod(s):"
while read -r ns name phase reason; do
    [ -n "$ns" ] && [ -n "$name" ] || continue
    # Unknown/NodeLost won't delete gracefully (kubelet/node never confirms); force
    # them. Safe here — at boot these are leftovers whose containers are already gone.
    if [ "$phase" = "Unknown" ] || [ "$reason" = "NodeLost" ]; then
        log "  ${ns}/${name} (${phase}/${reason}) — force"
        kubectl delete pod "$name" -n "$ns" --ignore-not-found --force --grace-period=0 2>&1 | tee -a "$LOG_FILE" || true
    else
        log "  ${ns}/${name} (${phase})"
        kubectl delete pod "$name" -n "$ns" --ignore-not-found 2>&1 | tee -a "$LOG_FILE" || true
    fi
done <<< "$targets"

log "Stale pod cleanup complete"
exit 0
