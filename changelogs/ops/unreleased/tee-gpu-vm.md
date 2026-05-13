### Added
- `ansible/host/playbooks/benchmark-setup.yml`: idempotent host-side setup playbook for benchmark VMs — syncs host-tools, installs `conntrack`, deploys the `benchmark-netlog` service, and starts logging. Safe to run while the VM is running; image build and launch remain manual steps.
- `ansible/host/roles/benchmark_vm`: single host role covering all benchmark VM host-side infrastructure. Derives the bridge subnet from `config.yaml` (or an explicit `benchmark_vm_bridge_subnet` override), installs `conntrack`, deploys `benchmark-netlog.sh` / `.service` / `.logrotate` from the synced host-tools checkout, writes `/etc/chutes/benchmark-netlog.env`, and enables + starts the service.
- `host-tools/scripts/network/benchmark-netlog.sh` / `.service` / `.logrotate`: host-side systemd service that streams `conntrack` events for the VM bridge subnet to daily log files under `/var/log/chutes/benchmark-netlog/`.
- `quick-launch.sh --benchmark` flag: sets benchmark defaults, skips cache volume, creates a network/hostname config volume, manages the `benchmark-netlog` service lifecycle, and validates config against `config-schema.benchmark.json`.
- `host-tools/scripts/config/config-schema.benchmark.json`: dedicated JSON schema for benchmark VM launch configs — omits `miner`, `volumes.cache`, `volumes.config`, and `docker_hub` fields which are not applicable.
- `host-tools/scripts/config/config.benchmark.example.yaml`: ready-to-use benchmark launch config template.

### Changed
- `create-config.sh`: miner SS58/seed arguments are now optional — both must be provided together or both left empty, so benchmark config volumes can be created without miner credentials using the same script.

### Fixed
- `passthrough.py` (`_run_gpu_tools`): GPU tools commands now run with a 120-second timeout and gracefully handle `TimeoutExpired`/`CalledProcessError` on reset failure, preventing indefinite hangs when a GPU is wedged at the PCIe level during VM teardown or reboot.
- `reset-gpus.sh`: aligned with updated `passthrough.py` error handling to avoid host lockups on stuck device resets.

### Removed
-
