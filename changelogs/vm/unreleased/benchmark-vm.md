### Added
- `playbooks/site-benchmark.yml`: dedicated benchmark VM build playbook. Fully
  independent of `site.yml` — includes only the roles a benchmark VM needs (`run-vm`,
  `common`, `gpu`, `benchmark`, `harden-access`, `security`, `cleanup`). No k3s,
  admission controller, system-manager, cache-volume, luks, or prime-vm plays.
- `benchmark` Ansible role: self-contained benchmark image setup. Now owns the full
  config volume stack (simplified `process-config.py`, `config-manager.service`,
  `var-config.mount`, `config-volume-validator.service`, `netplan-apply.service`) in
  addition to the TDX/GPU attestation tools, LUKS helper, and storage setup service.
- `attest` in-VM tool: `attest dump` prints TDX hardware measurements (MRTD, RTMRs,
  MRSEAM); `attest verify` adds NVIDIA NRAS GPU attestation (ES384-signed JWT) and
  optional Intel Tiber Trust Services TDX remote verification.
- `luks-setup` in-VM tool: `luks-setup setup` performs full end-to-end LUKS2
  encryption of the benchmark storage volume (wipe, encrypt, format XFS, mount at
  `/data`); `luks-setup open` unlocks it after a reboot. Both commands default to
  `/dev/chutes-storage` and `/data`, requiring no arguments in the common case.
  The volume is intentionally not persisted to crypttab/fstab — explicit unlock
  is required on every reboot.
- `benchmark-netlog` host-side systemd service: streams `conntrack` events for the
  VM bridge subnet to daily log files under `/var/log/chutes/benchmark-netlog/`,
  auto-installed by `quick-launch.sh --benchmark`.
- `quick-launch.sh --benchmark` flag: sets benchmark defaults, skips cache volume,
  creates a network/hostname config volume, and manages the netlog service lifecycle.
- `cleanup-benchmark-ssh.yml`: removes builder SSH keys, writes partner keys, and
  asserts key count and content before finalising the image.
- `config/config.benchmark.example.yaml`: ready-to-use launch config template.
- `config/config-schema.benchmark.json`: dedicated JSON schema for benchmark configs — omits `miner`, `volumes.cache`, `volumes.config`, and `docker_hub` which are not applicable. `quick-launch.sh --benchmark` automatically uses this schema during config validation.
- Benchmark VMs receive a config volume (hostname + network config) on launch via a simplified `process-config.py` that contains no k3s, miner credential, or Docker Hub logic. `create-config.sh` accepts empty miner credential arguments so the host-side config volume creation step is identical whether miner creds are provided or not.
- `serial-getty@ttyS0` and all virtual console getty services are now masked in benchmark images. `harden-access` now runs for benchmark builds; only the two SSH-related tasks (mask service, remove packages) are conditionally skipped via `when: not (benchmark_build ...)` since SSH is the only access path for partners.
- `benchmark-storage-setup.sh` + `setup-storage-bind-mounts.service` (benchmark role): at boot, identifies the storage block device, creates a stable `/dev/chutes-storage` symlink and `/data` mount point. Auto-mounts the device if it already has a filesystem; logs `luks-setup` instructions otherwise. Service name matches the production service so existing systemd ordering constraints are satisfied without any changes to `config-manager.service`.
- `docs/benchmark-vm.md`: operator reference for building and launching the benchmark VM.
- `docs/benchmark-guide.md`: user-facing walkthrough covering SSH access, GPU
  verification, attestation, storage encryption, and network transparency.

### Changed
- NVIDIA driver pin bumped from `595.58.03-1ubuntu1` to `595.71.05-1ubuntu1` (bug-fix
  release; resolves broken package state caused by base image advancing ahead of the pin).
- CUDA toolkit bumped from `13-0` to `13-2` (`cuda-toolkit-13-2` metapackage).
- `gpu/tasks/device-setup.yml`: Docker NVIDIA Container Runtime is now configured
  here (alongside containerd) so benchmark images have Docker GPU support without k3s.
- `roles/benchmark-attestation` consolidated and renamed to `roles/benchmark` — the
  single role for all benchmark-specific VM tooling.
- `docs/benchmark-mode.md` renamed to `docs/benchmark-vm.md`.
- `final_img_path` no longer appends a `-benchmark` suffix; use `build_env: "benchmark"`
  in inventory to produce a dedicated `image/benchmark/<version>.qcow2` output path.
- `site.yml` is now production-only — all `benchmark_build` conditions and the
  benchmark tools play have been removed. Benchmark builds use `site-benchmark.yml`.

### Fixed
- `process-config.py`: `apply_docker_hub_and_registries` now returns early with success when the `admission` group is absent and no Docker Hub credentials are present, instead of hard-failing. This prevented `config-manager.service` from starting in benchmark VMs (which have no admission controller).

### Removed
-
