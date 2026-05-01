### Added
- Benchmark VM build profile (`benchmark_build: true`) for NDA partner evaluation
  sessions. Builds a guest image with no Kubernetes orchestration, partner-provided
  SSH keys as the only authorised access, and no LUKS encryption.
- `benchmark` Ansible role: installs the TDX quote generator, `trustauthority-cli`,
  `chutes-nvevidence`, the `attest` verification script, and the `luks-setup` storage
  encryption helper into the benchmark image.
- `attest` in-VM tool: `attest dump` prints TDX hardware measurements (MRTD, RTMRs,
  MRSEAM); `attest verify` adds NVIDIA NRAS GPU attestation (ES384-signed JWT) and
  optional Intel Tiber Trust Services TDX remote verification.
- `luks-setup` in-VM tool: `luks-setup setup` performs full end-to-end LUKS2
  encryption of a storage disk (wipe, encrypt, format, mount, persist to crypttab
  and fstab); `luks-setup open` opens an existing encrypted device.
- `benchmark-netlog` host-side systemd service: streams `conntrack` events for the
  VM bridge subnet to daily log files under `/var/log/chutes/benchmark-netlog/`,
  auto-installed by `quick-launch.sh --benchmark`.
- `quick-launch.sh --benchmark` flag: sets benchmark defaults, skips cache/config
  volumes, and manages the netlog service lifecycle.
- `cleanup-benchmark-ssh.yml`: removes builder SSH keys, writes partner keys, and
  asserts key count and content before finalising the image.
- `config/config.benchmark.example.yaml`: ready-to-use launch config template.
- `config/config-schema.benchmark.json`: dedicated JSON schema for benchmark configs — omits `miner`, `volumes.cache`, `volumes.config`, and `docker_hub` which are not applicable. `quick-launch.sh --benchmark` automatically uses this schema during config validation.
- `docs/benchmark-vm.md`: operator reference for building and launching the benchmark VM.
- `docs/benchmark-guide.md`: user-facing walkthrough covering SSH access, GPU
  verification, attestation, storage encryption, and network transparency.

### Changed
- NVIDIA driver pin bumped from `595.58.03-1ubuntu1` to `595.71.05-1ubuntu1` (bug-fix
  release; resolves broken package state caused by base image advancing ahead of the pin).
- CUDA toolkit bumped from `13-0` to `13-2` (`cuda-toolkit-13-2` metapackage).
- `benchmark_build: true` now implicitly applies all debug-mode skips (no LUKS,
  no access hardening, no prime-vm); `debug_build` does not need to be set separately.
- `gpu/tasks/device-setup.yml`: Docker NVIDIA Container Runtime is now configured
  here (alongside containerd) so benchmark images have Docker GPU support without k3s.
- `roles/benchmark-attestation` consolidated and renamed to `roles/benchmark` — the
  single role for all benchmark-specific VM tooling.
- `docs/benchmark-mode.md` renamed to `docs/benchmark-vm.md`.
- `final_img_path` no longer appends a `-benchmark` suffix; use `build_env: "benchmark"`
  in inventory to produce a dedicated `image/benchmark/<version>.qcow2` output path.
- Security role (seccomp profiles, k3s containerd config) skipped for benchmark builds.

### Fixed
-

### Removed
-
