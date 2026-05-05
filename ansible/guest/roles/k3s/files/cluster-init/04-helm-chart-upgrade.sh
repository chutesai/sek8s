#!/bin/bash
# 04-helm-chart-upgrade.sh: Generic multi-chart Helm upgrade dispatcher.
#
# Loops through /etc/chutes/chart-versions/* to detect version drift against
# installed Helm releases and apply upgrades. Charts with custom upgrade logic
# (e.g. CRD migration) provide an override script in /etc/chutes/chart-upgrade-overrides/.
#
# Runs every boot (no .completed marker). Charts with matching versions exit early.
# Helm repos and HELM_*_HOME are pre-configured at build time by Ansible.
set -euo pipefail

LOG_FILE="/var/log/helm-chart-upgrade.log"
CHART_VERSIONS_DIR="/etc/chutes/chart-versions"
CHART_CONFIGS_DIR="/etc/chutes/chart-configs"
CHART_OVERRIDES_DIR="/etc/chutes/chart-upgrade-overrides"
KEYRING_FILE="/etc/chutes/helm-pubkey.gpg"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
# HELM_*_HOME are set by k3s-cluster-init.service; no fallbacks for determinism

export KUBECONFIG

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Wait for admission controller webhook to be ready (avoids "context deadline exceeded"
# for charts whose resources go through the webhook, e.g. chutes-miner-gpu).
wait_for_admission_webhook() {
    local max_attempts=30
    local n=0
    log "Waiting for admission webhook readiness..."
    while [ $n -lt $max_attempts ]; do
        if curl -sfk -o /dev/null "https://127.0.0.1:8443/health" 2>/dev/null; then
            log "Admission webhook is ready"
            return 0
        fi
        sleep 2
        n=$((n + 1))
    done
    log "WARNING: Admission webhook did not become ready in $((max_attempts * 2))s, proceeding anyway"
}

# Upgrade a single chart. Logs outcome; returns 0 on success (including no-op).
upgrade_chart() {
    local chart_name="$1"
    local marker_file="$CHART_VERSIONS_DIR/$chart_name"
    local config_file="$CHART_CONFIGS_DIR/${chart_name}.conf"
    local override_script="$CHART_OVERRIDES_DIR/${chart_name}.sh"

    if [ ! -f "$config_file" ]; then
        log "[$chart_name] No config file at $config_file, skipping"
        return 0
    fi

    # Load per-chart config: RELEASE, NAMESPACE, CHART, VERIFY, REUSE_VALUES
    RELEASE="" NAMESPACE="" CHART="" VERIFY="" REUSE_VALUES=""
    # shellcheck source=/dev/null
    source "$config_file"

    if [ -z "$RELEASE" ] || [ -z "$NAMESPACE" ] || [ -z "$CHART" ]; then
        log "[$chart_name] Config missing required fields (RELEASE, NAMESPACE, CHART), skipping"
        return 0
    fi

    local expected_version
    expected_version=$(tr -d '\n' < "$marker_file")

    # Query installed release by name within its namespace
    local helm_list_output helm_list_err helm_list_rc
    helm_list_err=$(mktemp)
    helm_list_rc=0
    helm_list_output=$(helm list -n "$NAMESPACE" -o json 2>"$helm_list_err") || helm_list_rc=$?

    if [ $helm_list_rc -ne 0 ]; then
        log "[$chart_name] WARNING: helm list failed (rc=$helm_list_rc): $(cat "$helm_list_err")"
        rm -f "$helm_list_err"
        log "[$chart_name] Skipping upgrade due to helm list failure"
        return 1
    fi
    rm -f "$helm_list_err"

    local release_json
    release_json=$(echo "$helm_list_output" \
        | jq -r --arg r "$RELEASE" '.[] | select(.name == $r)' || true)

    if [ -z "$release_json" ]; then
        log "[$chart_name] WARNING: Release '$RELEASE' not found in namespace '$NAMESPACE'. Skipping (release must be pre-installed at image build time)."
        log "[$chart_name] helm list returned: $helm_list_output"
        return 0
    fi

    local chart_full installed_version release_status
    chart_full=$(echo "$release_json" | jq -r '.chart // empty')
    installed_version="${chart_full#${chart_name}-}"
    release_status=$(echo "$release_json" | jq -r '.status // "deployed"')

    # Normalize leading 'v' for comparison (marker may use 'v26.3.1', helm list returns '26.3.1')
    local norm_installed norm_expected
    norm_installed="${installed_version#v}"
    norm_expected="${expected_version#v}"

    if [ "$norm_installed" = "$norm_expected" ] && [ "$release_status" != "failed" ]; then
        log "[$chart_name] Version matches (installed: $installed_version, status: $release_status), no upgrade needed"
        return 0
    fi

    if [ "$release_status" = "failed" ]; then
        log "[$chart_name] Release status is failed (installed: $installed_version), retrying upgrade"
    else
        log "[$chart_name] Version mismatch: installed=$installed_version expected=$expected_version, performing upgrade"
    fi

    # Delegate to override script if one exists for this chart
    if [ -x "$override_script" ]; then
        log "[$chart_name] Using custom upgrade script: $override_script"
        "$override_script" "$expected_version" "$installed_version"
        return $?
    fi

    # Default upgrade path: helm upgrade --install with flags from config
    if [ -n "$VERIFY" ] && [ ! -f "$KEYRING_FILE" ]; then
        log "[$chart_name] ERROR: VERIFY is set but keyring not found at $KEYRING_FILE"
        return 1
    fi

    local helm_args=(
        upgrade --install "$RELEASE" "$CHART"
        --namespace "$NAMESPACE"
        --version "$expected_version"
        --kubeconfig="$KUBECONFIG"
    )
    [ -n "$REUSE_VALUES" ] && helm_args+=(--reuse-values)
    [ -n "$VERIFY" ] && helm_args+=(--verify --keyring "$KEYRING_FILE")

    log "[$chart_name] Running: helm ${helm_args[*]}"
    helm "${helm_args[@]}"
}

main() {
    if [ ! -d "$CHART_VERSIONS_DIR" ]; then
        log "No chart versions directory at $CHART_VERSIONS_DIR, nothing to do"
        exit 0
    fi

    # Refresh all repo indexes once before processing charts
    log "Refreshing Helm repo index..."
    helm repo update

    wait_for_admission_webhook

    local overall_failed=0
    for marker_file in "$CHART_VERSIONS_DIR"/*; do
        [ -f "$marker_file" ] || continue
        local chart_name
        chart_name=$(basename "$marker_file")
        log "--- Processing chart: $chart_name ---"
        if upgrade_chart "$chart_name"; then
            log "[$chart_name] Done"
        else
            log "[$chart_name] FAILED"
            overall_failed=$((overall_failed + 1))
        fi
    done

    if [ $overall_failed -gt 0 ]; then
        log "Completed with $overall_failed chart upgrade failure(s)"
        exit 1
    fi

    log "All chart upgrades completed successfully"
}

main "$@"
