# Feature Spec: Helm Chart Auto-Upgrade on VM Boot

**Date**: 2026-03-21  
**Status**: implemented

---

## Context

Currently, helm charts (chutes-miner-gpu, gpu-operator, monitoring) are installed during the Ansible image build phase via `chutes-gpu` role tasks. The k3s cluster state (including installed helm releases) is persisted on the storage volume at `/var/lib/rancher/k3s`. When a new VM image ships with updated charts, miners must delete their storage volume to pick up the changes — forcing a full cluster re-initialization.

The boot-time cluster init system (`k3s-cluster-init.service`) runs numbered scripts from `/usr/local/bin/k3s-init-scripts/`. Scripts handle their own idempotency (run-once scripts use markers in `/var/lib/rancher/k3s/init-markers/`; run-every-boot scripts skip markers). This feature adds a new init script that compares a baked-in chart version marker against the version deployed in the cluster and runs `helm repo update` + `helm upgrade` when they differ.

- **Packages affected**: `ansible/k3s/roles/chutes-gpu`, `ansible/k3s/roles/k3s`, `ansible/k3s/roles/cleanup`
- **Key files**:
  - `ansible/k3s/roles/chutes-gpu/tasks/setup_chutes.yml` — build-time chart install
  - `ansible/k3s/roles/chutes-gpu/defaults/main.yml` — `chutes_chart_version` default
  - `ansible/k3s/roles/k3s/files/k3s-cluster-init.sh` — cluster init runner
  - `ansible/k3s/roles/k3s/files/cluster-init/*.sh` — numbered init scripts
  - `ansible/k3s/roles/cleanup/tasks/cleanup-k3s.yml` — marker cleanup at image build
- **Dependencies**: helm (already installed by `common` role), kubectl, k3s API readiness (handled by `k3s-cluster-init.sh`)

---

## Design Decisions

- **Version marker file on root filesystem (`/etc/chutes/chart-versions/chutes-miner-gpu`)**: A plain-text file baked into the root filesystem (not on the persistent storage volume) containing the expected chart version string. Because the root FS comes from the VM image, a new image always carries the latest version marker. The persistent storage volume retains the old cluster state, creating the version mismatch that triggers the upgrade.
- **Compare against installed release via `helm list`**: At boot, the init script queries `helm list -n chutes -o json` to extract the currently deployed chart version. If the installed version differs from the marker (or the release is missing), the script runs `helm repo update` and `helm upgrade --install`. This avoids relying on completed-markers for version tracking — the comparison is always live.
- **No completed-marker for this script**: Unlike the other init scripts (01–03) that use `.completed` markers and run only once, the helm upgrade script must run on every boot where a version mismatch exists. It should exit early (success) when versions match, but never write a `.completed` marker. This ensures it re-checks every boot.
- **Script ordering: `04-helm-chart-upgrade.sh`**: Runs after miner credentials (03) are created, since the chutes-miner-gpu chart may depend on the `miner-credentials` secret existing in the `chutes` namespace.
- **Only upgrade `chutes-miner-gpu` initially**: The gpu-operator and prometheus charts are pinned to stable versions and change rarely. This feature targets `chutes-miner-gpu` only. The pattern is extensible to other charts by adding additional marker files under `/etc/chutes/chart-versions/`.
- **Ansible writes the marker at build time**: The `chutes-gpu` role records the installed chart version into the marker file after a successful `helm upgrade --install`. This ensures the marker always reflects what was actually installed in the image.
- **Chart version is pinned for reproducibility**: `chutes_chart_version` in `chutes-gpu/defaults/main.yml` is pinned (e.g. `"0.1.0"`) rather than `null`. This makes builds reproducible and ensures the marker file has a well-defined value from one release to the next.
- **Helm repo exists at build time**: Ansible adds the `chutes` helm repo during the build phase. The boot script only runs `helm repo update` to refresh the index; no `helm repo add` is needed.

---

## API Changes

- **New endpoints**: None
- **Schema changes**: None
- **Migrations**: None

---

## Goal

Success = On VM boot with a persistent storage volume from an older image, the `chutes-miner-gpu` helm release is automatically upgraded to match the version baked into the new VM image — without the miner deleting their storage volume. Specifically:

1. A fresh boot with matching versions completes the init script in <5s with no helm operations.
2. A boot where the marker version differs from the installed release triggers `helm repo update` + `helm upgrade --install` and the release is updated to the marker version.
3. The upgrade preserves existing `miner-credentials` secret and runtime values — only the chart version changes.

---

## Constraints

- The init script must be a standalone bash script following `set -euo pipefail` conventions, matching the pattern of existing `cluster-init/*.sh` scripts.
- Must not use a `.completed` marker — the script re-evaluates version match on every boot.
- The marker file path must be on the root filesystem (not under `/var/lib/rancher/k3s/` which is bind-mounted from persistent storage).
- The marker file and this script are deployed together in the same image; the marker is always present when the script runs.
- On a fresh storage volume, `setup-storage-bind-mounts.sh` syncs the pre-installed k3s cluster state (including helm releases) from the root FS before k3s starts. The helm release will always exist by the time this script runs. The boot ordering (`setup-storage-bind-mounts.service` → `k3s.service` → `k3s-cluster-init.service`) guarantees this.
- The `helm upgrade --install` command must preserve the `--set-string minerCredentials.*=REPLACE_ME` and values-file pattern from `setup_chutes.yml` — the real credentials come from the k8s secret, not helm values.
- Script timeout: must complete within the existing `MAX_SCRIPT_TIMEOUT` (300s).

---

## Output Format

1. **New file: `ansible/k3s/roles/k3s/files/cluster-init/04-helm-chart-upgrade.sh`**
   - Reads expected version from `/etc/chutes/chart-versions/chutes-miner-gpu`
   - Queries installed version via `helm list -n chutes -o json | jq`
   - If versions differ: runs `helm repo update` then `helm upgrade --install chutes chutes/chutes-miner-gpu --namespace chutes --version <marker_version> --reuse-values --kubeconfig=/etc/rancher/k3s/k3s.yaml`
   - Logs all actions to `/var/log/helm-chart-upgrade.log`
   - Exits 0 on success or when versions already match

2. **Modified: `ansible/k3s/roles/chutes-gpu/tasks/setup_chutes.yml`**
   - After the existing `helm upgrade --install` task, add a task that queries the installed chart version (`helm list -n chutes -o json | jq -r '.[0].chart'` → extract version) and writes it to `/etc/chutes/chart-versions/chutes-miner-gpu`
   - Ensure `/etc/chutes/chart-versions/` directory is created

3. **Modified: `ansible/k3s/roles/k3s/files/k3s-cluster-init.sh`** (no change needed)
   - The existing `get_script_list()` function auto-discovers `*.sh` in the script dir via `find | sort -V`, so `04-helm-chart-upgrade.sh` is picked up automatically.

4. **Modified: `ansible/k3s/roles/cleanup/tasks/cleanup-k3s.yml`**
   - Do NOT clear `/etc/chutes/chart-versions/` during cleanup (it lives on root FS, not persistent storage). Verify no existing cleanup task removes `/etc/chutes/`.
   - (No changes needed — existing cleanup does not touch `/etc/chutes/`.)

5. **Helm repo**: Ansible adds the `chutes` repo at build time. The boot script runs `helm repo update` before upgrade to refresh the chart index.

---

## Failure Conditions

- The script runs `helm upgrade` when versions already match (wasted time, potential disruption).
- The script fails silently and the miner runs an outdated chart without any log output.
- The script writes a `.completed` marker, preventing re-evaluation on subsequent boots with newer images.
- The marker file ends up on the persistent storage volume instead of the root FS (defeats the purpose).
- The upgrade blows away runtime values (miner credentials, custom values) — must use `--reuse-values`.
- The script blocks boot indefinitely (must respect timeout, must not retry forever).

---

## Rollout Notes

- **First image with this feature**: Existing miners with old storage volumes will boot, the init script will find the marker file (new root FS) and detect a version mismatch against their persisted cluster state, triggering an automatic upgrade. No manual intervention needed.
- **Miners with no storage volume**: Normal first-boot flow. Charts are installed at build time, marker matches, no upgrade triggered.
- **`chutes_chart_version` Ansible var**: Pinned in defaults for reproducible builds. Override in group_vars for debug images (e.g. `0.1.0-dev.1`). The marker file reflects whatever version was actually installed.
- **Deployment**: The script and marker file are baked into the same image; both are deployed together. Rolling back to an older image removes both.
- **Future extension**: Additional charts can be added by placing version files under `/etc/chutes/chart-versions/<release-name>` and extending the script (or adding per-chart scripts).
