### Added
- Benchmark VM build profile (`benchmark_build: true`) for NDA partner evaluation
  sessions. Builds a guest image with no Kubernetes orchestration, partner-provided
  SSH keys as the only authorised access, and no LUKS encryption.
- `benchmark-attestation` Ansible role: installs the TDX quote generator,
  `trustauthority-cli`, `chutes-nvevidence`, and the `attest` verification script
  into the benchmark image.
- `attest` in-VM tool: `attest dump` prints TDX hardware measurements (MRTD, RTMRs,
  MRSEAM); `attest verify` adds NVIDIA NRAS GPU attestation (ES384-signed JWT) and
  optional Intel Tiber Trust Services TDX remote verification.
- `benchmark-netlog` host-side systemd service: streams `conntrack` events for the
  VM bridge subnet to daily log files under `/var/log/chutes/benchmark-netlog/`,
  auto-installed by `quick-launch.sh --benchmark`.
- `quick-launch.sh --benchmark` flag: sets benchmark defaults, skips cache/config
  volumes, and manages the netlog service lifecycle.
- `cleanup-benchmark-ssh.yml`: removes builder SSH keys, writes partner keys, and
  asserts key count and content before finalising the image.
- `config/config.benchmark.example.yaml`: ready-to-use launch config template.

### Changed
- `benchmark_build: true` now implicitly applies all debug-mode skips (no LUKS,
  no access hardening, no prime-vm); `debug_build` does not need to be set separately.
- `gpu/tasks/device-setup.yml`: Docker NVIDIA Container Runtime is now configured
  here (alongside containerd) so benchmark images have Docker GPU support without k3s.

### Fixed
-

### Removed
-
