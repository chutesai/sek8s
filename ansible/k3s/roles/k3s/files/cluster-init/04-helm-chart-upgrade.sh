#!/bin/bash
# 04-helm-chart-upgrade.sh: Upgrade chutes-miner-gpu helm release when image version differs from cluster.
# Runs every boot (no .completed marker). Exits early when versions match.
set -euo pipefail

LOG_FILE="/var/log/helm-chart-upgrade.log"
MARKER_FILE="/etc/chutes/chart-versions/chutes-miner-gpu"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
HELM_REPO_URL="https://chutesai.github.io/chutes-miner"
# k3s-cluster-init runs with ProtectHome=true; /root is not writable.
# Use paths under ReadWritePaths so helm never touches $HOME.
export HELM_CONFIG_HOME="/var/lib/rancher/k3s/helm-config"
export HELM_CACHE_HOME="/var/lib/rancher/k3s/helm-cache"
export HELM_DATA_HOME="/var/lib/rancher/k3s/helm-data"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

export KUBECONFIG

# Read expected version from marker (trim trailing newline)
expected_version=$(tr -d '\n' < "$MARKER_FILE")

# Get installed chart version and release status (format: chutes-miner-gpu-X.Y.Z)
release_json=$(helm list -n chutes -o json 2>/dev/null | jq -r '.[0] // empty' || true)
if [ -z "$release_json" ] || [ "$release_json" = "null" ]; then
    installed_version=""
    release_status=""
else
    chart_full=$(echo "$release_json" | jq -r '.chart // empty')
    installed_version="${chart_full#chutes-miner-gpu-}"
    release_status=$(echo "$release_json" | jq -r '.status // "deployed"')
fi

# No existing release: cannot upgrade. --reuse-values requires a release to reuse.
# Per the build process, the release is always pre-installed before this script runs.
# Missing release indicates a broken or unexpected cluster state.
if [ -z "$installed_version" ]; then
    log "ERROR: No chutes release found in namespace chutes. Refusing to run helm upgrade --install with --reuse-values (nothing to reuse). Check that setup-storage-bind-mounts ran and the cluster state was synced."
    exit 1
fi

# Versions match and release is healthy - no action needed
# If status is "failed", retry upgrade even when versions match (e.g. previous attempt hit webhook timeout)
if [ "$installed_version" = "$expected_version" ] && [ "$release_status" != "failed" ]; then
    log "Versions match (installed: $installed_version, status: $release_status), no upgrade needed"
    exit 0
fi

if [ "$release_status" = "failed" ]; then
    log "Release status is failed (versions match: $installed_version), retrying upgrade"
fi

if [ "$installed_version" != "$expected_version" ]; then
    log "Version mismatch: installed=$installed_version expected=$expected_version, performing upgrade"
fi

# Wait for admission controller webhook to be ready (avoids "context deadline exceeded")
log "Waiting for admission webhook readiness..."
admission_max=30
admission_n=0
while [ $admission_n -lt $admission_max ]; do
    if curl -sfk -o /dev/null "https://127.0.0.1:8443/health" 2>/dev/null; then
        log "Admission webhook is ready"
        break
    fi
    sleep 2
    admission_n=$((admission_n + 1))
done
if [ $admission_n -ge $admission_max ]; then
    log "WARNING: Admission webhook did not become ready in ${admission_max}s, proceeding anyway"
fi

# k3s-cluster-init runs with ProtectHome=true and cannot read /root/.config/helm where
# Ansible added the repo at build time. HELM_CONFIG_HOME points to a writable path.
helm repo add chutes "$HELM_REPO_URL"
helm repo update

log "Running helm upgrade --install..."
helm upgrade --install chutes chutes/chutes-miner-gpu \
    --namespace chutes \
    --version "$expected_version" \
    --reuse-values \
    --kubeconfig="$KUBECONFIG"

log "Helm chart upgrade completed successfully"
exit 0
