#!/bin/bash
# 04-helm-chart-upgrade.sh: Upgrade chutes-miner-gpu helm release when image version differs from cluster.
# Runs every boot (no .completed marker). Exits early when versions match.
set -euo pipefail

LOG_FILE="/var/log/helm-chart-upgrade.log"
MARKER_FILE="/etc/chutes/chart-versions/chutes-miner-gpu"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

export KUBECONFIG

# Read expected version from marker (trim trailing newline)
expected_version=$(tr -d '\n' < "$MARKER_FILE")

# Get installed chart version (format: chutes-miner-gpu-X.Y.Z)
chart_full=$(helm list -n chutes -o json 2>/dev/null | jq -r '.[0].chart // empty' || true)
if [ -z "$chart_full" ] || [ "$chart_full" = "null" ]; then
    installed_version=""
else
    installed_version="${chart_full#chutes-miner-gpu-}"
fi

# Versions match - no action needed
if [ "$installed_version" = "$expected_version" ]; then
    log "Versions match (installed: $installed_version), no upgrade needed"
    exit 0
fi

log "Version mismatch: installed=$installed_version expected=$expected_version, performing upgrade"

helm repo update

log "Running helm upgrade --install..."
helm upgrade --install chutes chutes/chutes-miner-gpu \
    --namespace chutes \
    --version "$expected_version" \
    --reuse-values \
    --kubeconfig="$KUBECONFIG"

log "Helm chart upgrade completed successfully"
exit 0
