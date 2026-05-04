# Ops Changelog

Operational tooling changes: `ansible/host/`, `host-tools/`, `.github/workflows/`.
Date-stamped entries, not paired with any VERSION file. Run `make promote-changelogs` to aggregate fragments.

## [2026-05-04]

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

## [2026-04-23]

### Added
- Add firewall playbook to setup default firewall for host
- Added support to create changelog for current branchin make commands

### Changed
- Added python3 venv deps to host
- Update host tools to handle divergent branches

### Fixed
- Fixed handlign of stale venv if previous ansible run had an issue in host setup playbooks.

### Removed
-

## [2026-04-20]

### Added
- Operational Ansible under `ansible/host/` (setup, launch, upgrade, shutdown) for bare-metal TDX hosts.
- QEMU duplicate-instance guard in `quick-launch.sh`.
- `chutes_vm_config`: volume `path` variables (`chutes_volume_cache_path`, `chutes_volume_storage_path`, `chutes_volume_config_path`) now render into `config.yaml` instead of hardcoded empty strings, allowing per-host volume path overrides via inventory `host_vars` or `group_vars`.

### Changed
- Renamed guest image build Ansible directory from `ansible/k3s/` to `ansible/guest/`; VM `VERSION` path is now `ansible/guest/VERSION`.
- `chutes_vm_config`: network CIDR detection (`pick_guest_network.py`) now only runs on first launch. Subsequent runs read `bridge_ip`, `vm_ip`, and `public_interface` from the existing `config.yaml` via `slurp` + `from_yaml`, preventing a new subnet from being assigned and accumulating stale bridge IPs on `br0` on every re-run.
- `shutdown.yml`: full graceful shutdown sequence — lock server (`chutes-miner lock`), purge deployments (`chutes-miner purge-deployments`), poll until chute pods are gone, then issue shutdown and wait for power-down in the guest serial log.
- `upgrade.yml`: replaced manual `kubectl delete pods` drain block with a poll-until-empty using `--context {{ chutes_tee_server_name }}` and label `chutes/chute=true`. Validator drains pods automatically after `start-maintenance`; playbook now waits for that to complete rather than forcing deletion.
- `shutdown_via_miner.yml`: scope reduced to issue-shutdown + wait-for-power-down only. Lock and drain are the caller's responsibility, allowing `shutdown.yml` (manual drain) and `upgrade.yml` (validator-managed drain) to use different strategies.

### Fixed
- `setup.yml`: `tdx_bootstrap` now always runs `setup-tdx-host` unconditionally instead of falling back to `--install-tools-only` when the TDX module is already initialized.
- `tdx_bootstrap`: reboot detection now also compares `uname -r` against the `saved_entry` in `/boot/grub/grubenv` to avoid skipping reboots when `update-notifier-common` is absent.
- `security-verified-path-gate.yml`: `git diff` now uses `-M --diff-filter=ACM` so pure renames are not treated as new release-scoped files.
- `.gitignore`: added `.vscode/` and removed tracked IDE settings files.

