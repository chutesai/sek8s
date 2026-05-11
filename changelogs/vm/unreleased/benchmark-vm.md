### Added
- `roles/rtmr3-measure`: new standalone role that extends TDX RTMR3 at boot
  with SHA-384 hashes of a configurable list of paths (defaults: SSH keys,
  passwd, shadow, sudoers).  An `initramfs-tools` hook bakes the measurement
  script and path config into the initramfs (covered by RTMR1) so neither can
  be tampered with without changing RTMR1.  Any offline modification to a
  measured path — e.g. SSH key injection into an unencrypted image — produces
  a different RTMR3 that verifiers can detect at session start.
- `tdx-rtmr-extend`: small C binary installed to `/usr/local/bin/` that
  extends a TDX RTMR via `/dev/tdx_guest` ioctl (V2 and V3 ABIs) with a
  sysfs fallback for kernels ≥ 6.16.  Does not require libtdx-attest at
  runtime.
- `verify-access-config`: Python script installed to `/usr/local/bin/`
  for use by the partner inside the VM.  Displays SSH keys (fingerprints +
  comments), sshd config, user accounts, password status, and sudo rules.
  Replays the SHA-384 extend chain to compute the expected RTMR3, reads the
  live value from a TDX quote, and reports PASS/FAIL.  The script itself is
  in the measurement list so any tampering changes RTMR3.
- `chutes-miner-vm.yml` and `tee-gpu-vm.yml`: added `rtmr3-measure` play after
  security hardening and before cleanup so the final on-disk state (including
  partner SSH keys written by cleanup) is what gets measured at boot.
- `playbooks/tee-gpu-vm.yml`: dedicated benchmark VM build playbook. Fully
  independent of `chutes-miner-vm.yml` — includes only the roles a benchmark VM needs (`run-vm`,
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
- `ansible/host/playbooks/benchmark-setup.yml`: idempotent host setup playbook for benchmark VMs. Safe to run while the VM is running. Syncs host-tools, installs `conntrack`, deploys the `benchmark-netlog` service, and starts logging. The benchmark image is built and launched manually — this playbook only handles host-side infrastructure.
- `ansible/host/roles/benchmark_vm`: single role covering all host-side benchmark VM setup. Derives the bridge subnet from the existing `config.yaml` on the host (or an explicit `benchmark_vm_bridge_subnet` override), installs `conntrack`, deploys `benchmark-netlog.sh` / `.service` / `.logrotate` from the synced host-tools checkout, writes `/etc/chutes/benchmark-netlog.env`, and enables + starts the service. Handlers trigger `daemon-reload` + restart on any file or config change.
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
- `chutes-miner-vm.yml` is now production-only — all `benchmark_build` conditions and the
  benchmark tools play have been removed. Benchmark builds use `tee-gpu-vm.yml`.

### Fixed
- `process-config.py`: `apply_docker_hub_and_registries` now returns early with success when the `admission` group is absent and no Docker Hub credentials are present, instead of hard-failing. This prevented `config-manager.service` from starting in benchmark VMs (which have no admission controller).
- `attest verify`: GPU attestation now passes `options={"ppcie_mode": False}` to `get_evidence()`, matching how `chutes_nvevidence.NvClient.gather_evidence()` works — fixes evidence collection on H200s in Protected PCIe mode.
- `attest verify`: TDX verification replaced `trustauthority-cli` with `dcap_qvl.get_collateral_and_verify()` — no API key or config file required, same library used in production validator. `dcap-qvl` is now installed in the nvevidence venv.
- `docs/benchmark-guide.md`: corrected GPU verification command (`nvidia-smi conf-compute -q`, not `-s`), updated description for PPCIe mode, removed references to `trustauthority-cli` and Intel API key config.

### Removed
-
