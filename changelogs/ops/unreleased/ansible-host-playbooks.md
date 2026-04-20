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
