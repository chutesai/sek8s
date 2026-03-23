# Feature Spec: Helm Chart Auto-Upgrade on VM Boot

**Date**: 2026-03-21  
**Status**: implemented

---

## Context

Currently, helm charts (chutes-miner-gpu, gpu-operator, monitoring) are installed during the Ansible image build phase via `chutes-gpu` role tasks. The k3s cluster state (including installed helm releases) is persisted on the storage volume at `/var/lib/rancher/k3s`. When a new VM image ships with updated charts, miners must delete their storage volume to pick up the changes — forcing a full cluster re-initialization.

The boot-time cluster init system (`k3s-cluster-init.service`) runs numbered scripts from `/usr/local/bin/k3s-init-scripts/`. Scripts handle their own idempotency (run-once scripts use markers in `/var/lib/rancher/k3s/init-markers/`; run-every-boot scripts skip markers). This feature adds a new init script that compares a baked-in chart version marker against the version deployed in the cluster and runs `helm repo update` + `helm upgrade` when they differ.

- **Packages affected**: `ansible/k3s/roles/chutes-gpu`, `ansible/k3s/roles/k3s`, `ansible/k3s/roles/cleanup`
- **Key files**:
  - `ansible/k3s/roles/chutes-gpu/tasks/setup_chutes.yml` — build-time chart install, Helm env, repo add
  - `ansible/k3s/roles/chutes-gpu/defaults/main.yml` — `chutes_chart_version`, `helm_chart_public_key_path`, `chutes_helm_repo_url`
  - `helm_chart_public_key_path` — build-time var pointing to PGP public key file (required)
  - `ansible/k3s/roles/k3s/defaults/main.yml` — `helm_config_home`, `helm_cache_home`, `helm_data_home`
  - `ansible/k3s/roles/chutes-gpu/defaults/main.yml` — same (for standalone runs)
  - `ansible/k3s/roles/k3s/templates/k3s-cluster-init.service.j2` — Helm env vars for child scripts
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
- **Build-time Helm environment pre-configuration**: Ansible creates `/var/lib/chutes/helm-config`, `helm-cache`, `helm-data` (on root volume, VM-version-specific) and runs `helm repo add chutes <url>` + `helm repo update` at build time with `HELM_CONFIG_HOME`/`HELM_CACHE_HOME`/`HELM_DATA_HOME` pointing to those paths. The repo config and cached index are baked into the image. The `k3s-cluster-init` service exports these env vars; the boot script inherits them and does not run `helm repo add`. It only runs `helm repo update` (index refresh) and `helm upgrade --install`. Helm artifacts live on root, not on the storage volume, so no sync is needed — each new VM image carries the correct helm config for that version.
- **PGP chart provenance (required)**: An attacker who can redirect `chutesai.github.io` (DNS hijack, BGP, CA compromise) could serve a malicious chart with a matching version string. Helm natively supports `--verify` with `.prov` provenance files. The PGP public keyring at `/etc/chutes/helm-pubkey.gpg` is required; both build-time install and boot-time upgrade use `--verify --keyring`. If the keyring is missing at boot, the script exits 1 (fail closed). The `chutesai/chutes-miner` chart repo must sign releases with `helm package --sign` and publish `.tgz.prov` files (external prerequisite).

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
- Helm env vars (`HELM_CONFIG_HOME`, `HELM_CACHE_HOME`, `HELM_DATA_HOME`) are set in the systemd service unit; scripts inherit them and must not override.
- The build-time `helm repo add` uses the same `HELM_CONFIG_HOME` path the service uses at runtime. Helm config/cache/data live on the root volume (`/var/lib/chutes/helm-*`), not on storage, so they are VM-version-specific and require no sync.
- PGP keyring at `/etc/chutes/helm-pubkey.gpg` must exist; if missing at boot, script exits 1. Provenance verification failure is fatal (exit 1, fail closed). Chart repo must publish `.prov` files.

---

## Output Format

1. **Modified: `ansible/k3s/roles/k3s/files/k3s-cluster-init.service`**
   - Add `Environment=HELM_CONFIG_HOME=/var/lib/chutes/helm-config`, `HELM_CACHE_HOME=.../helm-cache`, `HELM_DATA_HOME=.../helm-data` to the `[Service]` section so child scripts inherit them.

2. **Modified: `ansible/k3s/roles/chutes-gpu/tasks/setup_chutes.yml`**
   - Before helm install: create `/etc/chutes/`; copy PGP keyring from `helm_chart_public_key_path` to `/etc/chutes/helm-pubkey.gpg`; create `helm-config`, `helm-cache`, `helm-data` under `/var/lib/chutes/` (root volume, VM-version-specific).
   - Replace `kubernetes.core.helm_repository` with shell tasks that run `helm repo add chutes <url>` and `helm repo update` with `HELM_*_HOME` env vars set.
   - Add `--verify --keyring /etc/chutes/helm-pubkey.gpg` to `helm upgrade --install` (always).
   - After helm install: query installed version and write to `/etc/chutes/chart-versions/chutes-miner-gpu`.

3. **Build-time config: `helm_chart_public_key_path`**
   - Path to PGP public key file (like `cosign_public_key_path`). Required. Ansible copies this file to `/etc/chutes/helm-pubkey.gpg` in the image.

4. **Modified: `ansible/k3s/roles/k3s/files/cluster-init/04-helm-chart-upgrade.sh`**
   - Uses `HELM_*_HOME` from service unit only (no fallbacks); does not run `helm repo add`.
   - Requires keyring at `/etc/chutes/helm-pubkey.gpg`; exits 1 if missing. Runs `helm repo update`, then `helm upgrade --install` with `--verify --keyring` (always).
   - Reads expected version from marker; queries installed version; exits 0 when versions match and release is healthy; otherwise runs upgrade. Logs to `/var/log/helm-chart-upgrade.log`.

5. **Modified: `ansible/k3s/roles/k3s/files/k3s-cluster-init.sh`** (no change needed)
   - Auto-discovers `*.sh` in the script dir; `04-helm-chart-upgrade.sh` is picked up automatically.

6. **Modified: `ansible/k3s/roles/cleanup/tasks/cleanup-k3s.yml`**
   - Do NOT clear `/etc/chutes/` during cleanup (version marker and keyring live on root FS). (No changes needed — existing cleanup does not touch `/etc/chutes/`.)

---

## Failure Conditions

- The script runs `helm upgrade` when versions already match (wasted time, potential disruption).
- The script fails silently and the miner runs an outdated chart without any log output.
- The script writes a `.completed` marker, preventing re-evaluation on subsequent boots with newer images.
- The marker file ends up on the persistent storage volume instead of the root FS (defeats the purpose).
- The upgrade blows away runtime values (miner credentials, custom values) — must use `--reuse-values`.
- The script blocks boot indefinitely (must respect timeout, must not retry forever).
- **No existing release**: If `helm list -n chutes` returns no release, the script exits 1. Running `helm upgrade --install --reuse-values` with no prior release would perform a fresh install using chart defaults (nothing to reuse), which could overwrite or miss runtime configuration. The build process guarantees the release exists; missing release indicates broken cluster state (e.g. setup-storage-bind-mounts did not run).
- **Provenance verification**: Chart provenance verification fails (missing `.prov`, wrong signature, tampered chart) — script exits 1. PGP keyring missing — script exits 1 (fail closed). Chart repo publishes unsigned releases — upgrade fails until provenance is added.
- **Helm repo config**: If build-time `helm repo add` did not run — `helm repo update` fails at boot, script exits 1. (Helm config lives on root volume, so no sync or storage migration concerns.)

---

## Rollout Notes

- **First image with this feature**: Existing miners with old storage volumes will boot, the init script will find the marker file (new root FS) and detect a version mismatch against their persisted cluster state, triggering an automatic upgrade. No manual intervention needed.
- **Miners with no storage volume**: Normal first-boot flow. Charts are installed at build time, marker matches, no upgrade triggered.
- **`chutes_chart_version` Ansible var**: Pinned in defaults for reproducible builds. Override in group_vars for debug images (e.g. `0.1.0-dev.1`). The marker file reflects whatever version was actually installed.
- **Deployment**: The script and marker file are baked into the same image; both are deployed together. Rolling back to an older image removes both.
- **Future extension**: Additional charts can be added by placing version files under `/etc/chutes/chart-versions/<release-name>` and extending the script (or adding per-chart scripts).
- **PGP provenance**: Keyring is always required. Configure `helm_chart_public_key_path` to point to the PGP public key (e.g. `~/.chutes/helm-pubkey.gpg`). At boot, the script fails if the keyring is missing. **Upstream prerequisite**: Chart repo must publish `.prov` files.
- **Key rotation**: Publish a new VM image with the updated keyring. Old images trust the old key until replaced.
- **Helm env**: The script relies on `HELM_*_HOME` from the service unit; no fallbacks. Old images without these env vars will fail if the script runs (helm would try `~/.config/helm` which is not writable under `ProtectHome`). Upgrade to an image that includes the service unit changes.
