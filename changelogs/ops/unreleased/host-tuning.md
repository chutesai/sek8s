### Added
- Standalone host CPU tuning tool (`python -m chutes.host.tune apply|restore`, exposed as the `chutes-tune-host` / `chutes-restore-host` commands via the existing `setup-tdx-host` tool-symlink step), decoupled from VM launch — applied and reverted deliberately by the operator rather than automatically on every start/stop. Implements the NVIDIA Confidential Computing Deployment Guide (DU-12302-001) host-OS recommendation: CPU frequency governor → `performance` and C-states **C1E/C6** disabled, leaving the shallow `POLL`/`C1` states enabled for thermal headroom (it no longer disables every C-state, nor forces turbo/EPP). Pre-tuning values are snapshotted to `/var/lib/chutes/tdx-host-tuning-restore.sh`; re-running `apply` reapplies settings without overwriting the saved original state.
- Automatic GPU profile detection from host PCI/sysfs topology, with multi-GPU host support — new `detect_host_cpus()`, `detect_host_sockets()`, and `detect_numa_node_count()` helpers read CPU count, socket count, and NUMA layout from sysfs to select and verify the correct `GpuProfile`.

### Changed
- NVSwitch-based B200/B300 profiles now declare `requires_fabric_manager`; host setup starts (or restarts) `nvidia-fabricmanager.service` immediately rather than only enabling it, so the NVSwitch fabric is active before launch.
- InfiniBand passthrough is now optional: a host whose profile supports IB passthrough but exposes no IB devices logs a note and skips passthrough instead of aborting the launch.
- `discover-profile.sh` now reports detected CPU socket and NUMA topology alongside the existing GPU, PCI BAR, and firmware values.

### Fixed
- Host OOM-kill of the VM under load: the per-NUMA-node guest memory backends no longer set `prealloc=on`. Under TDX the guest's RAM is private memory served lazily from `guest_memfd`, so preallocating the memory-backend pinned a second full copy of pages the guest never uses as shared (~2× guest RAM), which OOM-killed QEMU as a pod warmed up. NUMA locality (`host-nodes=…,policy=bind`) is preserved. Affected all NUMA profiles (H200/B200/B300).
- VM launch now aborts with a clear error instead of OOM-killing the host when a profile's fixed guest RAM cannot physically fit (host RAM minus reserve). Guest RAM stays a fixed, profile-determined value (it feeds the guest ACPI tables and thus TDX measurements), so this check never resizes the VM.
- Fabric Manager `PARTITION_RAIL_POLICY` is now set to the string `symmetric` required by FM 595+ instead of the numeric `1` that newer FM rejects (CC mode would otherwise stay silently disabled on Blackwell).
- Pinned the Fabric Manager package to `595.71.05-0ubuntu0.26.04.1` (full distro-qualified version) to match the guest driver pin and stop apt from installing a mismatched build.
