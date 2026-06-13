#!/bin/bash
# 05-attestation-proxy-recovery.sh
#
# Runs every boot (intentionally NOT marker-guarded) before 99-purge-kubeconfig.sh,
# while the admin kubeconfig still exists.
#
# After an ungraceful reboot the attestation-proxy DaemonSet Pod is frequently
# left in an "Unknown" phase (or with status.reason=NodeLost): the kubelet that
# owned it is gone, so the DaemonSet controller never replaces it and the
# critical proxy stays down with no way to recover on its own. Force-delete any
# such stuck Pod so the DaemonSet immediately recreates a fresh one.
#
# Best-effort ONLY: k3s-cluster-init powers the VM off on any non-zero script
# exit, so every step is guarded and this script always exits 0. Pods that are
# Pending/Running (legitimately starting or waiting on their init container) are
# left untouched so we never kill one that is making progress.

KUBECTL="${KUBECTL:-kubectl}"
NS="${ATTESTATION_NS:-attestation-system}"
SELECTOR="${ATTESTATION_SELECTOR:-app=attestation-proxy}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [05-attestation-proxy-recovery] $1"
}

rows="$("$KUBECTL" get pods -n "$NS" -l "$SELECTOR" \
    -o 'jsonpath={range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\t"}{.status.reason}{"\n"}{end}' \
    2>/dev/null)"
if [ $? -ne 0 ]; then
    log "WARNING: could not list pods in $NS; skipping recovery"
    exit 0
fi

if [ -z "$rows" ]; then
    log "No attestation-proxy pods found in $NS; nothing to do"
    exit 0
fi

while IFS=$'\t' read -r name phase reason; do
    [ -n "$name" ] || continue
    case "$phase:$reason" in
        Unknown:*|Failed:*|*:NodeLost)
            log "Force-deleting stuck pod $name (phase=$phase reason=${reason:-<none>})"
            if "$KUBECTL" delete pod "$name" -n "$NS" --force --grace-period=0 >/dev/null 2>&1; then
                log "Deleted $name; DaemonSet will recreate it"
            else
                log "WARNING: failed to delete stuck pod $name"
            fi
            ;;
        *)
            log "Pod $name is healthy or progressing (phase=$phase reason=${reason:-<none>}); leaving as-is"
            ;;
    esac
done <<EOF
$rows
EOF

exit 0
