### Added
- Opt-in host CPU performance tuning (`runtime.tune_host`, default off; `chutes_runtime_tune_host` Ansible var). On VM launch it sets the CPU governor and energy-performance-preference to `performance`, enables Turbo Boost, and disables C-states. The pre-tuning values are snapshotted to `/var/lib/chutes/tdx-host-tuning-restore.sh` and restored when the VM stops; re-launching an already-tuned host reapplies settings without overwriting the saved original state.
- Automatic GPU profile detection from host PCI/sysfs topology, with multi-GPU host support — new `detect_host_cpus()`, `detect_host_sockets()`, and `detect_numa_node_count()` helpers read CPU count, socket count, and NUMA layout from sysfs to select and verify the correct `GpuProfile`.

### Changed
- NVSwitch-based B200/B300 profiles now declare `requires_fabric_manager`; host setup starts (or restarts) `nvidia-fabricmanager.service` immediately rather than only enabling it, so the NVSwitch fabric is active before launch.
- InfiniBand passthrough is now optional: a host whose profile supports IB passthrough but exposes no IB devices logs a note and skips passthrough instead of aborting the launch.
- `discover-profile.sh` now reports detected CPU socket and NUMA topology alongside the existing GPU, PCI BAR, and firmware values.
- Host CPU tuning no longer depends on `cpupower` (`linux-tools-common`); the governor and energy-performance-preference are written directly via sysfs.

### Fixed
- Fabric Manager `PARTITION_RAIL_POLICY` is now set to the string `symmetric` required by FM 595+ instead of the numeric `1` that newer FM rejects (CC mode would otherwise stay silently disabled on Blackwell).
- Pinned the Fabric Manager package to `595.71.05-0ubuntu0.26.04.1` (full distro-qualified version) to match the guest driver pin and stop apt from installing a mismatched build.
