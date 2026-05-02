### Added
- `upgrade-host.yml` playbook for automated, version-aware multi-hop OS upgrades. Follows the `os_upgrade_path` registry in `group_vars/all.yml`, supports `auto_drain_vm` (default `false`) to drain/shutdown the VM before upgrading, prompts for operator confirmation, and relaunches the VM with node-health verification after the final hop.
- `os_upgrade` Ansible role with a common `hop.yml` skeleton (disk check, pre-hook, dist-upgrade, do-release-upgrade, re-provision) and version-specific pre-upgrade hooks `pre_2504.yml` and `pre_2510.yml` that remove stale kobuk PPA sources and purge old attestation packages before `apt` touches the index.
- `chutes_tee_vm/tasks/launch_and_verify.yml` shared task: renders `config.yaml`, downloads base image if missing, runs `quick-launch.sh`, and polls `node-health`. Used by `launch.yml`, `upgrade-guest.yml`, and `upgrade-host.yml`.
- `chutes_tee_vm/tasks/drain_and_shutdown.yml` shared task: idempotent maintenance-mode check, full drain sequence, and graceful VM shutdown — gated on VM liveness so re-runs skip already-completed steps.
- `os_upgrade_path` map in `group_vars/all.yml` defining supported upgrade hops (`25.04 → 25.10`, `25.10 → 26.04`).
- `os_upgrade/defaults/main.yml` with `auto_drain_vm: false` and `relaunch_vm: true` role defaults (inventory overrides both).

### Changed
- Renamed `upgrade.yml` → `upgrade-guest.yml`; updated all README and playbook references.
- `Ubuntu2510Profile`: removed kobuk attestation PPA, added Intel DCAP repo (`https://download.01.org/intel-sgx/sgx_repo/ubuntu/`, suite `noble`) via the `repos` property introduced in PR #67.
- `HostProfile` base class: added default `repos` property returning `[]` to prevent `AttributeError` when `setup_host()` iterates repos on profiles that don't define one.
- `drain_and_shutdown.yml`: added `--miner-api` flag to `start-maintenance` (was silently defaulting to `127.0.0.1:32000` regardless of inventory); added maintenance-status idempotency check; gated sync-kubeconfig, pod-drain wait, and shutdown on VM liveness to allow clean re-runs.
- `launch.yml`: simplified tasks section to use `launch_and_verify.yml`; added `chutes_hotkey_path` as a required variable.
- `upgrade-guest.yml`: replaced inline config-render/quick-launch/health-poll block with `launch_and_verify.yml`.
- `tdx_bootstrap` and post-upgrade reboot timeouts increased to 1800s (30 min); post-do-release-upgrade reboot timeout increased to 3600s (60 min) to accommodate slow first-boot fsck on large drives.
- Post-upgrade host provisioning (`host_prerequisites` + `tdx_bootstrap`) now only runs on the final hop of a multi-hop upgrade, skipping redundant provisioning of intermediate OS versions.
- `assert_not_running.yml`: `when` condition now uses `| default(1)` to handle `--check` mode where `rc` is undefined.

### Removed
- `Ubuntu2504Profile` and `KOBUK_TEAM_KEY` from `host/profiles.py`: Ubuntu 25.04 reached EOL January 2026. Hosts still on 25.04 should use `upgrade-host.yml` to advance to 25.10 or 26.04.
