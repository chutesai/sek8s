#!/bin/bash
# 04-helm-chart-upgrade.sh: Boot-time Helm chart reconciler.
#
# Each /etc/chutes/charts/<name>.conf is the single source of truth for one chart
# (version + values + flags), git-tracked and measured into RTMR3 so it cannot be
# tampered with. On every boot a chart is reconciled via `helm upgrade` when the
# conf's content hash differs from the last-applied marker (or the release is in a
# `failed` state). Unchanged confs are skipped, so there is no helm-revision churn.
# Because the trigger is the conf hash, a VERSION bump and a values change are
# handled identically — no separate "overrides" or "updates" mechanism needed.
#
# Markers live on the persisted storage volume so "applied" survives reboots and
# image updates (/var/lib/rancher/k3s is bind-mounted from storage):
#   /var/lib/rancher/k3s/init-markers/charts/<name>
#
# Conf fields (shell-sourced):
#   RELEASE, NAMESPACE, CHART   (required)
#   VERSION                     (REQUIRED — exact chart version to pin. Never resolved to
#                                "latest" or the installed version: the measured spec must
#                                fully determine what runs, so a third party auditing the
#                                VM can reproduce it. A values-only change still pins the
#                                same VERSION explicitly.)
#   HELM_SET                    (space-separated key=value -> --set; for small overrides.
#                                Prefer a values file: /etc/chutes/charts/values/<name>.yaml
#                                is auto-detected and passed via -f. Both the conf and the
#                                values file are hashed for the change-trigger.)
#   EXTRA_FLAGS                 (extra helm CLI flags, e.g. --disable-openapi-validation)
#   REUSE_VALUES                (non-empty -> --reuse-values)
#   VERIFY                      (non-empty -> --verify --keyring <KEYRING_FILE>)
#   REPO_NAME, REPO_URL         (optional; `helm repo add` before upgrade)
#   FATAL                       ("true" -> a reconcile failure powers off the VM;
#                                otherwise best-effort, logged, retried next boot)
set -uo pipefail

LOG_FILE="/var/log/helm-chart-upgrade.log"
CHARTS_DIR="/etc/chutes/charts"
MARKERS_DIR="/var/lib/rancher/k3s/init-markers/charts"
KEYRING_FILE="/etc/chutes/helm-pubkey.gpg"
KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export KUBECONFIG
# HELM_*_HOME are set by k3s-post-start.service; no fallbacks for determinism

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

# Wait for admission controller webhook (charts whose resources go through it,
# e.g. chutes-miner-gpu, otherwise fail with "context deadline exceeded").
wait_for_admission_webhook() {
    local max_attempts=30 n=0
    log "Waiting for admission webhook readiness..."
    while [ "$n" -lt "$max_attempts" ]; do
        if curl -sfk -o /dev/null "https://127.0.0.1:8443/health" 2>/dev/null; then
            log "Admission webhook is ready"
            return 0
        fi
        sleep 2
        n=$((n + 1))
    done
    log "WARNING: Admission webhook not ready in $((max_attempts * 2))s, proceeding anyway"
}

# Reconcile one chart conf. Returns 0 on success/skip, 1 on failure.
reconcile_chart() {
    local conf="$1"
    local name marker
    name="$(basename "$conf" .conf)"
    marker="$MARKERS_DIR/$name"

    RELEASE="" NAMESPACE="" CHART="" VERSION="" HELM_SET="" EXTRA_FLAGS=""
    REUSE_VALUES="" VERIFY="" REPO_NAME="" REPO_URL="" FATAL=""
    # shellcheck source=/dev/null
    source "$conf"
    if [ -z "$RELEASE" ] || [ -z "$NAMESPACE" ] || [ -z "$CHART" ]; then
        log "[$name] missing RELEASE/NAMESPACE/CHART — skipping"
        return 0
    fi
    # VERSION is mandatory: never run helm without a pinned version (no "latest"),
    # so the measured spec is fully reproducible for third-party audit.
    if [ -z "$VERSION" ]; then
        log "[$name] VERSION is required (pinned for reproducibility) — refusing to reconcile"
        return 1
    fi

    # Values come from values/<name>.yaml (preferred, readable/diffable) and/or
    # HELM_SET. Hash the conf AND the values file together so a change to either
    # triggers a reconcile.
    local want_hash cur_hash status values_file
    values_file="$CHARTS_DIR/values/$name.yaml"
    want_hash="$( { cat "$conf"; [ -f "$values_file" ] && cat "$values_file"; } 2>/dev/null \
        | sha256sum | awk '{print $1}')"
    cur_hash="$(cat "$marker" 2>/dev/null || true)"
    status="$(helm list -n "$NAMESPACE" -o json 2>/dev/null \
        | jq -r --arg r "$RELEASE" '.[] | select(.name==$r) | .status // empty' 2>/dev/null || true)"

    # Reconcile a build-time install; never create. No marker on miss -> retries.
    if [ -z "$status" ]; then
        log "[$name] release '$RELEASE' not found in '$NAMESPACE' — skipping (retries next boot)"
        return 0
    fi

    if [ "$want_hash" = "$cur_hash" ] && [ "$status" != "failed" ]; then
        log "[$name] spec unchanged (status=$status) — skipping"
        return 0
    fi

    if [ -n "$REPO_NAME" ] && [ -n "$REPO_URL" ]; then
        helm repo add "$REPO_NAME" "$REPO_URL" >/dev/null 2>&1 || true
        helm repo update "$REPO_NAME" >/dev/null 2>&1 || helm repo update >/dev/null 2>&1 || true
    fi

    if [ -n "$VERIFY" ] && [ ! -f "$KEYRING_FILE" ]; then
        log "[$name] VERIFY set but keyring missing at $KEYRING_FILE — FAILED"
        return 1
    fi

    local args=(upgrade "$RELEASE" "$CHART" --namespace "$NAMESPACE" --kubeconfig="$KUBECONFIG")
    [ -n "$VERSION" ] && args+=(--version "$VERSION")
    [ -n "$REUSE_VALUES" ] && args+=(--reuse-values)
    [ -n "$VERIFY" ] && args+=(--verify --keyring "$KEYRING_FILE")
    [ -f "$values_file" ] && args+=(-f "$values_file")
    local kv
    for kv in $HELM_SET; do args+=(--set "$kv"); done
    # EXTRA_FLAGS is intentionally word-split into separate flags.
    # shellcheck disable=SC2206
    [ -n "$EXTRA_FLAGS" ] && args+=($EXTRA_FLAGS)

    log "[$name] reconciling (status=$status, version=$VERSION): helm ${args[*]}"
    if helm "${args[@]}"; then
        mkdir -p "$MARKERS_DIR"
        printf '%s\n' "$want_hash" > "$marker"
        log "[$name] reconciled; marker updated"
        return 0
    fi
    log "[$name] helm upgrade FAILED"
    return 1
}

# Read the FATAL field of a conf in isolation (no side effects on the caller).
conf_is_fatal() {
    ( set +u; FATAL=""; . "$1" >/dev/null 2>&1; [ "${FATAL:-}" = "true" ] )
}

main() {
    if [ ! -d "$CHARTS_DIR" ]; then
        log "No charts directory at $CHARTS_DIR, nothing to do"
        exit 0
    fi

    log "Refreshing Helm repo index..."
    helm repo update >/dev/null 2>&1 || true
    wait_for_admission_webhook

    local nonfatal_failed=0 conf name
    for conf in "$CHARTS_DIR"/*.conf; do
        [ -f "$conf" ] || continue
        name="$(basename "$conf" .conf)"
        log "--- Reconciling chart: $name ---"
        if reconcile_chart "$conf"; then
            log "[$name] OK"
        elif conf_is_fatal "$conf"; then
            log "FATAL: required chart $name failed to reconcile — powering off VM"
            echo "CHART-RECONCILE-FAILED: $name" > /dev/kmsg 2>/dev/null || true
            poweroff -f
        else
            log "[$name] FAILED (non-fatal; retries next boot)"
            nonfatal_failed=$((nonfatal_failed + 1))
        fi
    done

    log "Chart reconcile complete ($nonfatal_failed non-fatal failure(s))"
    exit 0
}

main "$@"
