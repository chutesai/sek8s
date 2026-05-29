# Ops Changelog

Operational tooling changes: `ansible/host/`, `host-tools/`, `.github/workflows/`.
Versioned with CalVer `YYYY.MM.PATCH` via `changelogs/ops/VERSION`. Run `make promote-changelogs` to aggregate fragments into the current version section.

## [2026.05.2] - 2026-05-29

### Added
- B200 GPU support: host-side Fabric Manager setup in `chutes.host.setup` — detects B200 GPUs at runtime and installs `nvidia-fabricmanager`, `nvlsm`, `libibumad3`, `infiniband-diags`, configures `ib_umad` autoload and `PARTITION_RAIL_POLICY=1` in `fabricmanager.cfg`, and enables `nvidia-fabricmanager.service`.
- `detect_cx7_bridge_pfs()` in `chutes.guest.detection` — identifies ConnectX-7 NVSwitch bridge PFs via VPD `SMDL=SW_MNG` field so they can be excluded from guest passthrough.
- CX7 NVSwitch bridge PF exclusion in `setup_passthrough()` — bridge PFs are logged and excluded before IB NIC VFs are created, preventing accidental VFIO binding of FM-managed devices.
- `(25.10, B200, 8)` added to validated topology matrix in `support_matrix.py`.
- `ansible/host/roles/ntp` — new Ansible role that installs chrony, masks `systemd-timesyncd`, and configures `makestep 1 -1` so large clock offsets (e.g. BMC RTC set ahead) are stepped immediately on first boot rather than slowly slewed. Wired as the first role in `setup.yml`.
- `playbooks/launch.yml` now verifies host clock offset is within 5 seconds via `chronyc tracking` before launching the VM. A skewed host clock causes VMs to boot with the wrong time, which breaks boot-time mTLS cert validation at the attestation endpoint.

### Changed
- `B200Profile` corrected: `host_cpus=192`, `host_sockets=2`, `bar_size_mb=262144` (256 GiB BAR confirmed from hardware).
- `detect_infiniband_pfs()` now accepts an optional `exclude_bdfs` parameter.

### Fixed
- `ansible/host/roles/pccs_configure`: fixed automated SGX platform registration via `PCKIDRetrievalTool`.
  Two bugs were addressed:
  1. `PCKIDRetrievalTool` was piped a password via stdin which the tool ignores (reads from `/dev/tty`);
     replaced with the `-user_token` CLI flag.
  2. PCCS ≤1.26 intentionally downgrades Intel PCS requests for QE/QVE identity and TCB to v3 when a
     v3-format response is needed, but Intel retired the v3 PCS API in 2026 (returns HTTP 410). This caused
     PCCS to return 404 to `PCKIDRetrievalTool` before its own v4 retry completed. A patch task now removes
     the two downgrade blocks from `pcs_client.js` so PCCS always uses v4; the data format is identical and
     v3 PCCS API clients continue to work. Upstream fix submitted as
     [intel/confidential-computing.tee.dcap.pccs#52](https://github.com/intel/confidential-computing.tee.dcap.pccs/pull/52).
     The patch task will be removed once that PR is merged and a fixed package ships.

## [2026.05.1] - 2026-05-21

### Fixed
- `drain_and_shutdown.yml`: pass `stdin: "y\n"` to the CLI drain command to satisfy the confirmation prompt introduced in the latest CLI version.
- `shutdown_via_miner.yml`: replace serial-log grep for "Power down" with `is_live_chutes_td.sh` script polling; loop now succeeds when the QEMU guest process exits (`rc != 0`) rather than waiting for a log message that may not appear.

## [2026.05.0] - 2026-05-18

### Added
- `ansible/host/playbooks/benchmark-setup.yml`: idempotent host-side setup playbook for benchmark VMs — syncs host-tools, installs `conntrack`, deploys the `benchmark-netlog` service, and starts logging. Safe to run while the VM is running; image build and launch remain manual steps.
- `ansible/host/roles/benchmark_vm`: single host role covering all benchmark VM host-side infrastructure. Derives the bridge subnet from `config.yaml` (or an explicit `benchmark_vm_bridge_subnet` override), installs `conntrack`, deploys `benchmark-netlog.sh` / `.service` / `.logrotate` from the synced host-tools checkout, writes `/etc/chutes/benchmark-netlog.env`, and enables + starts the service.
- `host-tools/scripts/network/benchmark-netlog.sh` / `.service` / `.logrotate`: host-side systemd service that streams `conntrack` events for the VM bridge subnet to daily log files under `/var/log/chutes/benchmark-netlog/`.
- `quick-launch.sh --benchmark` flag: sets benchmark defaults, skips cache volume, creates a network/hostname config volume, manages the `benchmark-netlog` service lifecycle, and validates config against `config-schema.benchmark.json`.
- `host-tools/scripts/config/config-schema.benchmark.json`: dedicated JSON schema for benchmark VM launch configs — omits `miner`, `volumes.cache`, `volumes.config`, and `docker_hub` fields which are not applicable.
- `host-tools/scripts/config/config.benchmark.example.yaml`: ready-to-use benchmark launch config template.
- **GPU profile SMP topology**: `GpuProfile` now derives an accurate `smp_topology`
  string from `host_cpus` and `host_sockets` and passes it to QEMU via `-smp`. This
  matches the physical NUMA topology of the host CPU, improving vCPU scheduling.
  Profile authors only need to set `host_cpus` and `host_sockets`; `vcpus` and
  `smp_topology` are derived automatically.
- **`chutes.guest launch --ssh` flag**: SSH login hint is now opt-in via `--ssh`
  rather than always printed, keeping standard launch output clean.
- **Benchmark config validation**: `chutes.guest.config` accepts a `--benchmark`
  flag to validate against the benchmark-specific JSON schema instead of the default
  miner schema.
- **Ansible timing callbacks**: `ansible.cfg` now enables
  `ansible.posix.timer` and `ansible.posix.profile_tasks` callbacks, surfacing
  per-task timing in playbook output.

### Changed
- `chutes_vm_config` role: `base_image` and `overlay_directory` in `config.yaml` are now driven by `chutes_vm_base_image` and `chutes_vm_overlay_directory` Ansible variables (both default to `""`, preserving the existing behaviour of letting host-tools use its built-in defaults).
- `create-config.sh`: miner SS58/seed arguments are now optional — both must be provided together or both left empty, so benchmark config volumes can be created without miner credentials using the same script.
- `ansible/guest/roles/k3s`: absorbed all k3s/k8s prerequisite tasks previously scattered across the `common` and `security` roles — k3s networking (UFW rules, iptables compatibility), k3s directory/registry config, k8s tooling, helm install, and seccomp profiles now live entirely within the `k3s` role. Playbooks compose roles without build-type flags.
- `ansible/guest/roles/common`: slimmed to base system setup only (`system.yml`, `mirror.yml`, `container-networking.yml`). Container networking tasks (br_netfilter, overlay, bridge sysctl, AppArmor) kept here as they support Docker in all builds; renamed `kubernetes.conf` module persistence file to `container-modules.conf`.
- `ansible/guest/roles/security`: removed `seccomp-profiles` tasks and files; role now only performs chroot/init hardening and cloud-init disabling, unconditionally.
- `ansible/guest/inventory.yml`: removed `benchmark_build` flag — build type is now determined entirely by playbook selection.
- `ansible/guest/inventory.yml`, `ansible/guest/playbooks/tee-gpu-vm.yml`, `ansible/guest/roles/setup-ssh-access/`: renamed `benchmark_ssh_keys` to `guest_ssh_keys`.
- **build-setup playbook**: Installs `ansible` package via apt and adds the
  `host_prerequisites` role, simplifying first-time host preparation.
- **upgrade-guest playbook**: Passes the pre-validated image SHA to the
  `launch_and_verify` task when upgrading from a freshly-downloaded image, avoiding
  a redundant SHA recomputation.
- `pre_2510.yml`: adds explicit gating so SGX/DCAP packages that were updated via `unattended-upgrades` are preserved rather than purged during the 25.04 → 25.10 hop; only packages that were not updated get removed

### Fixed
- `passthrough.py` (`_run_gpu_tools`): GPU tools commands now run with a 120-second timeout and gracefully handle `TimeoutExpired`/`CalledProcessError` on reset failure, preventing indefinite hangs when a GPU is wedged at the PCIe level during VM teardown or reboot.
- `reset-gpus.sh`: aligned with updated `passthrough.py` error handling to avoid host lockups on stuck device resets.
- `pre_2504.yml`: `grub-common` is now pinned/held before the OS upgrade begins to prevent apt from purging it during dependency resolution — loss of `grub-common` left hosts unbootable
- `pre_2504.yml` / `hop.yml` / `post_2504.yml`: corrected task ordering so DKMS kernel module rebuilds and `grub-pc` reconfiguration occur in the right sequence for both fresh 25.04 installs and the 25.04 → 25.10 hop, eliminating boot failures caused by stale module state
- QEMU was always launched with `sockets=1` regardless of the physical host topology. On 2-socket servers (H200, RTX PRO 6000) this caused QEMU to emit a degenerate CPUID with no core or package topology levels, triggering the kernel `arch topology borken` warning on every vCPU at boot and presenting a misleading scheduler topology to workloads inside the VM

## [2026-05-07]

### Added
- `remediate-host.yml` playbook: remediates an existing TDX host in-place without an OS upgrade; detects the current Ubuntu version and runs the matching `pre_<version>.yml` cleanup hook (shared with `upgrade-host.yml`), then re-provisions via `host_prerequisites` → `host_tools` → `tdx_bootstrap` → `pccs_configure`
- `setup-tdx-host --noninteractive` flag: suppresses apt/debconf prompts when invoked by Ansible so `pccs_configure` can handle PCCS configuration externally; manual runs remain interactive so the PCCS debconf prompts work as normal
- `upgrade-guest.yml`: `relaunch_vm` flag (default `true`) — set `false` to promote the image and leave the VM down (e.g. when chaining with `remediate-host.yml`)
- `upgrade-guest.yml` / `remediate-host.yml`: `force_upgrade` flag — when the validator denies maintenance (e.g. `sole_survivor`), calls `purge-server` to notify the control plane, waits up to 5 minutes for natural drain, force-evicts any remaining pods, then retries maintenance
- `drain_and_shutdown`: human-readable maintenance-denial message surfacing reason and blocking instance IDs instead of a raw JSON dump
- `launch_and_verify`: pre-flight SHA256 check against the expected hash from `quick-launch.sh` before attempting VM start; fails with a clear message pointing to `upgrade-guest.yml` when the image is stale
- `host_prerequisites`: installs `git` so `host_tools` (which runs `git fetch`) always has it available

### Changed
- `setup.yml` and `remediate-host.yml`: `host_prerequisites` now runs before `host_tools` (correct ordering — prerequisites must be installed before the repo sync that depends on them)
- `pccs_configure` role: replaced purge+reinstall-on-broken-node_modules logic with an explicit `npm install --prefer-offline` task; purge+reinstall is now reserved for the case where `package.json` itself is absent
- `pccs_configure` role: generates TLS certificate as `file.crt` (was `certificate.pem`) to match the filename PCCS expects by default
- `pre_2510.yml`: removes `/opt/intel/sgx-dcap-pccs/` directory after purging packages so the reinstall starts with a clean slate and `npm install` runs correctly
- `setup-tdx-host` / `setup.py`: removed `_purge_conflicting_sgx_packages()` — migration cleanup is a host-upgrade concern owned by `pre_2510.yml`, not the vanilla-OS setup script

### Fixed
- PCCS service failing to start after non-interactive install: `sgx-dcap-pccs` Debian post-install only runs `npm install` during interactive debconf prompts; non-interactive installs silently skipped it, leaving `node_modules/` empty
- PCCS TLS: `ssl_key/certificate.pem` generated by the role was ignored by PCCS which hardcodes `ssl_key/file.crt`

## [2026-05-06]

### Fixed
- `setup-tdx-host` now configures QGS for vsock mode (`port = 4050` in `/etc/qgs.conf`) on all hosts; the Intel-shipped default leaves this commented out, silently breaking TDX quote generation in VMs
- `setup-tdx-host` now sets `use_secure_cert: false` in `/etc/sgx_default_qcnl.conf` so QGS can reach the local PCCS instance which uses a self-signed certificate

## [2026-05-05]

### Added
- Layered guest image build caching: `prepared_img_path` (offline kernel install via `virt-customize`) and `gpu_img_path` (NVIDIA drivers + CUDA) checkpoint layers using `virsh suspend`/`resume`. K3s version bumps now resume from `gpu_img_path`, skipping the expensive NVIDIA driver reinstall.
- `save-checkpoint` Ansible role: reusable virsh suspend → disk copy → resume sequence; restarts `systemd-networkd` and `systemd-resolved` after resume to recover VM network state.
- `build-setup.yml` playbook: one-shot host preparation for VM image building — benchmarks regional apt mirrors and pins the fastest via the `host_mirror` role.
- Dynamic apt mirror selection in both host (`host_mirror` role) and guest (`common/mirror.yml`): benchmarks `archive.ubuntu.com` regional mirrors at build time; override by setting `apt_mirror` in inventory.
- `NO_CACHE` env var support: invalidates all cached image layers and forces a full rebuild from the base Ubuntu cloud image.

### Changed
- GPU role split from K3s-aware monolith: NVIDIA container runtime for K3s (`nvidia-runtime.yml`) moved into K3s role and runs conditionally when `nvidia-container-toolkit` is installed. GPU role now contains only driver/CUDA/InfiniBand setup.
- GPU checkpoint now saved after drivers only; K3s installs after the checkpoint. Previously K3s was baked into `gpu_img_path`, forcing driver reinstall on every K3s upgrade.
- Intermediate image paths (`prepared_img_path`, `gpu_img_path`) namespaced under `{{ img_dir }}/{{ build_env }}/{{ vm_version }}-*.qcow2` to prevent overwrites when managing multiple build versions on the same host.
- TDX host check in `quick-launch.sh` now checks sysfs (`/sys/module/kvm_intel/parameters/tdx=Y`) first, then `/proc/cpuinfo`, dmesg as last resort — avoids false negatives on long-running hosts where the dmesg ring buffer has rolled over.

### Fixed
-

### Removed
-

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

