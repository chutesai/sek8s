### Added

- **Benchmark VM host playbook**: New `ansible/host/playbooks/benchmark-setup.yml`
  deploys the host-side infrastructure for a running benchmark TDX VM — installs
  conntrack, deploys the `benchmark-netlog` service and logrotate config, and writes
  the bridge subnet env file. Idempotent and safe to re-run while the VM is running.

- **Benchmark netlog service**: Network logging service (`benchmark-netlog.sh`,
  systemd unit, logrotate config) deployed via the new `benchmark_vm` Ansible role.
  Tracks per-connection byte counts on the VM bridge interface.

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

- **build-setup playbook**: Installs `ansible` package via apt and adds the
  `host_prerequisites` role, simplifying first-time host preparation.

- **upgrade-guest playbook**: Passes the pre-validated image SHA to the
  `launch_and_verify` task when upgrading from a freshly-downloaded image, avoiding
  a redundant SHA recomputation.
