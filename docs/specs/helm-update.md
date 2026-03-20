# Feature Spec: Remote Helm Chart Update

**Date**: 2026-03-20
**Status**: draft

---

## Context

- **Packages affected**: `sek8s/system_manager/` (new `helm/` sub-package), `ansible/k3s/roles/system-manager/`
- **Key files**:
  - `sek8s/services/manager.py` -- mount new `helm_router` at `/helm`
  - New `sek8s/system_manager/helm/` -- `manager.py`, `router.py`, `models.py`, `responses.py`
  - New `ansible/k3s/roles/system-manager/files/k3s-helm-helper` -- restricted wrapper script
  - `ansible/k3s/roles/system-manager/tasks/main.yml` -- install helper, sudoers, kubeconfig access
  - `ansible/k3s/roles/chutes-gpu/tasks/setup_chutes.yml` -- reference for current chart install logic
  - `ansible/k3s/roles/chutes-gpu/defaults/main.yml` -- current chart config (repo URL, version, values)
- **Dependencies**: Helm binary already at `/usr/bin/helm` (installed by `ansible/k3s/roles/common/tasks/helm.yml`)

---

## Design Decisions

### 1. TDX measurements are not affected by helm upgrades

Changes to the root filesystem (source code in `/opt`, static manifests, package updates, etc.) **do** change TDX measurements empirically. However, `helm upgrade` does not modify root filesystem files. It modifies Kubernetes resources stored in k3s etcd -- Helm release Secrets, Deployments, Services, ConfigMaps, etc. The k3s database lives on the encrypted storage volume (`/cache/storage/k3s`, bind-mounted over `/var/lib/rancher/k3s`), which is a separate volume unlocked after attestation, not part of the measured root image.

**Attestation continues to pass after helm upgrades because helm only writes to the k3s database on the storage volume, never to the root filesystem.**

### 2. Restricted wrapper script (`k3s-helm-helper`)

Follow the `k3s-images-helper` pattern. A root-owned bash script at `/usr/local/bin/k3s-helm-helper` that:

- Only allows subcommands: `repo-update`, `upgrade`, `list`, `status`
- Hardcodes `--kubeconfig=/etc/rancher/k3s/k3s.yaml`
- Hardcodes the allowed helm repo name (`chutes`) and URL (`https://chutesai.github.io/chutes-miner`)
- Hardcodes the allowed release-to-chart mapping: `chutes` release -> `chutes/chutes-miner-gpu` chart, `chutes` namespace
- **Version validation via repo lookup, not input sanitization**: The `upgrade` subcommand first runs `helm search repo chutes/chutes-miner-gpu` to get the list of available versions. The caller-supplied version string is compared via exact string match against the parsed `CHART VERSION` column. If it matches, the version from the parsed output (not the caller input) is passed to `helm upgrade --version`. If no match, the script exits non-zero. If no version is requested, the latest from the search output is used. User input is never evaluated or interpolated into a command -- it is only used as a lookup key.
- Uses `--reuse-values` on upgrade so existing miner credentials and validator config are preserved
- Runs with sudo (unlike the images helper which uses containerd group) because helm needs kubeconfig read access; the restricted script keeps this safe

### 3. Version-only upgrades, no arbitrary values, no user input in commands

The API only accepts a chart version (optional, defaults to latest). `--reuse-values` preserves all current values. No values override mechanism through the API. The version string from the API is **never** interpolated into any command. The wrapper script runs `helm search repo` to get available versions, does an exact string comparison against the caller's requested version, and if matched, uses the version parsed from helm's own output. This means even a compromised API caller cannot inject anything into the helm command line -- their input is only ever compared, never evaluated. If values need updating, that's a separate concern handled by config-manager or a future spec.

### 4. Auth: miner or validator

Both the miner and validator can trigger upgrades. `authorize(allow_miner=True, allow_validator=True, purpose="helm")`.

### 5. Async upgrade tracking

Same pattern as `ImageManager._pull_tasks` -- upgrade runs in a background `asyncio.Task`, status is queryable via `GET /helm/upgrade/status`.

---

## API Changes

### New endpoints (mounted at `/helm`)

`**POST /helm/upgrade`** -- Start a helm upgrade

- Auth: miner or validator
- Request body: `{ "release": "chutes", "version": "0.2.1" }` (version optional; release must be in allowlist)
- Response: `{ "release": "chutes", "status": "started" | "in_progress" | "up_to_date" }`
- Calls `k3s-helm-helper upgrade chutes [version]`

`**GET /helm/releases**` -- List helm releases

- Auth: miner or validator
- Response: `{ "releases": [{ "name": "chutes", "namespace": "chutes", "chart": "chutes-miner-gpu-0.2.1", "status": "deployed", "revision": 3, "updated": "2026-03-20T..." }] }`
- Calls `k3s-helm-helper list`

`**GET /helm/releases/{name}/status**` -- Get release status

- Auth: miner or validator
- Response: detailed release info (status, revision, chart version, app version)
- Calls `k3s-helm-helper status <name>`

`**GET /helm/upgrade/status**` -- Get async upgrade operation status

- Auth: miner or validator
- Response: `{ "release": "chutes", "status": "in_progress" | "completed" | "failed", "error": null }`
- **Schema changes**: None
- **Migrations**: None

---

## Goal

Success = all of the following:

1. Miner or validator can call `POST /helm/upgrade` to upgrade the chutes chart to a specific version (or latest)
2. Upgrade preserves existing values (miner credentials, validator config) via `--reuse-values`
3. Container images already downloaded on the storage volume are preserved (no storage volume deletion needed)
4. TDX attestation passes after upgrade (no root filesystem changes, only k3s DB state on storage volume)
5. The wrapper script prevents arbitrary helm commands, chart repos, or value injection
6. `GET /helm/releases` and `GET /helm/releases/{name}/status` return accurate release info

---

## Constraints

- Wrapper script uses `set -euo pipefail`, `shell=False` on the Python side
- Only the `chutes` release (from `chutes/chutes-miner-gpu`) is allowed; other release names are rejected by the wrapper script with a non-zero exit
- Version is validated by exact match against `helm search repo` output; the caller's version string is never passed to any command, only compared. The version used in `helm upgrade --version` always comes from helm's own search output.
- No `--set`, `--set-string`, `-f`, or `--values` flags are ever passed; only `--reuse-values`
- Upgrade is async (background task) with a 10-minute timeout; only one upgrade can run at a time
- `system-manager` runs the helper via sudo; sudoers entry restricts to exactly `/usr/local/bin/k3s-helm-helper`

---

## Output Format

1. `**ansible/k3s/roles/system-manager/files/k3s-helm-helper`** -- Bash wrapper script (~80 lines). Subcommands: `repo-update`, `upgrade <version?>`, `list`, `status <release>`. Hardcoded allowlist, kubeconfig path, repo URL, release-to-chart mapping. The `upgrade` subcommand runs `helm search repo` first, parses available versions, does exact string match against the requested version, and only uses the parsed version from helm output in the actual `helm upgrade` command.
2. `**sek8s/system_manager/helm/manager.py**` -- `HelmManager` class. Methods: `list_releases()`, `get_release_status(name)`, `start_upgrade(release, version)`, `get_upgrade_status()`. Calls `k3s-helm-helper` via `asyncio.create_subprocess_exec` with sudo. Tracks async upgrade in `_upgrade_task` / `_upgrade_result`.
3. `**sek8s/system_manager/helm/router.py**` -- FastAPI `APIRouter`. Endpoints: `POST /upgrade`, `GET /releases`, `GET /releases/{name}/status`, `GET /upgrade/status`. Uses `authorize(allow_miner=True, allow_validator=True, purpose="helm")`. Depends on `HelmManager` from `request.app.state.helm_manager`.
4. `**sek8s/system_manager/helm/models.py**` -- Request/internal models: `UpgradeRequest(release, version?)`, `UpgradeStatusEnum`, `UpgradeSnapshot`, `ReleaseEntry`.
5. `**sek8s/system_manager/helm/responses.py**` -- Response schemas: `UpgradeStartResponse`, `UpgradeStatusResponse`, `ReleaseListResponse`, `ReleaseStatusResponse`.
6. `**sek8s/system_manager/helm/__init__.py**` -- Package init.
7. `**sek8s/services/manager.py**` -- Add `helm_router` import and `include_router(helm_router, prefix="/helm", tags=["helm"])`. Initialize `HelmManager` in lifespan.
8. `**ansible/k3s/roles/system-manager/tasks/main.yml**` -- Install `k3s-helm-helper`, add sudoers entry for it.

---

## Failure Conditions

- Wrapper script allows any chart repository other than `chutes`
- Wrapper script allows any release name other than the hardcoded allowlist
- Arbitrary values can be injected via the API or wrapper script
- `--reuse-values` is not used, causing miner credentials to be lost on upgrade
- User-supplied version string is ever interpolated into a command (must only be used for string comparison against `helm search repo` output)
- Helm commands run without `--kubeconfig` (would fail or use wrong cluster)
- Upgrade blocks the event loop (must be async)
- Multiple concurrent upgrades can run (must be serialized)
- TDX attestation fails after upgrade (should not happen per analysis, but must be tested)

---

## Rollout Notes

- **Ansible changes**: Add `k3s-helm-helper` to system-manager role files, add sudoers entry `system-manager ALL=(ALL) NOPASSWD: /usr/local/bin/k3s-helm-helper`
- **Backward compatible**: Existing VMs without the helper script simply won't have the `/helm` endpoints functional; the system-manager service still starts fine (helper absence causes 502 on helm endpoints, not a crash)
- **Feature gating**: Consider an env var `HELM_UPDATE_ENABLED=true` in `system-manager.env` to disable on older deployments
- **Test plan**: (1) Upgrade chutes chart on running VM, verify pods restart with new chart, (2) verify attestation still passes after upgrade, (3) verify wrapper rejects disallowed release names/repos, (4) verify a non-existent version is rejected (no match in `helm search repo` output), (5) verify the version actually used in the helm command comes from parsed search output, not from caller input
- **VM image rebuild still needed for**: any root filesystem change -- kernel, initrd, TDVF, k3s binary, GPU driver, system service code, static manifests, sek8s source in `/opt`, etc. These all change TDX measurements. Helm update only covers chart-level changes (Kubernetes resources stored in k3s etcd on the storage volume, not on the root filesystem).

