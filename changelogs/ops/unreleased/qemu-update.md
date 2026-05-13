### Added
- `GpuProfile.host_sockets` property (default `1`) and `GpuProfile.smp_topology` property that derives the full QEMU `-smp` string from the physical socket layout, so the guest CPU topology mirrors the host's CPUID structure
- `H200Profile` and `RTXPro6000Profile` override `host_sockets = 2` to reflect their 128-CPU / 2-socket servers
- `profiles.py` module docstring documents how to derive `host_cpus` and `host_sockets` from `lscpu` output when adding a new GPU profile

### Changed
- `build_base_cmd` (`qemu.py`) accepts `smp_topology: str` instead of `vcpus: str`; the caller in `__main__.py` passes the topology string from the active `GpuProfile` (or a flat single-socket default for non-GPU VMs)

### Fixed
- QEMU was always launched with `sockets=1` regardless of the physical host topology. On 2-socket servers (H200, RTX PRO 6000) this caused QEMU to emit a degenerate CPUID with no core or package topology levels, triggering the kernel `arch topology borken` warning on every vCPU at boot and presenting a misleading scheduler topology to workloads inside the VM
