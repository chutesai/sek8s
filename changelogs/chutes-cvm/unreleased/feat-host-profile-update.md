### Changed

- `discover-profile.sh` emits per-device lists — `gpus`, `nvswitches`, `ib_devices` — each object
  carrying its own BDF, vendor/device/class, NUMA node and BAR layout. Previously the document
  described a platform through parallel arrays (`gpu.bdfs`, `gpu.numa_nodes`, `nic.ib_devices`,
  …) whose entries had to be zipped back together by index at every reader. BAR sizes come from
  world-readable `/sys/bus/pci/devices/*/resource`, so the capture still runs unprivileged and
  works with the GPUs already bound to `vfio-pci`. The legacy blocks are still emitted during the
  transition.
- New `chutes_cvm/guest/devices.py` models those lists (`PciBar`, `GpuDevice`, `NvSwitchDevice`,
  `IbDevice`). Every key is required — defaulting a missing one would turn a malformed document
  into a device with no identity — and devices sort by BDF at construction, so ordering is an
  invariant rather than something each caller re-establishes.
- New `chutes_cvm/guest/host_profile.py` is the single reader of a capture. `HostProfile` holds
  the raw document and resolves everything derived from it — guest CPU/RAM, `-smp` topology,
  NUMA vectors, which endpoints the profile actually passes through, and the QEMU command
  itself. Launch, offline measurement generation and profile submission all build from it, so
  the three paths can no longer drift on how they read the same host.
- Renamed the `launch_determinism` block to `qemu` and dropped its `numa_node_count`,
  `numa_topology_eligible`, `cpu_args` and `host_cpu_topology` members. The name fit when the
  block also carried the guest `-smp` string; that left when reserved CPUs became per-profile.
  The four dropped members either restated `numa`/`cpu` or were re-derived by every reader, and
  nothing read them. What remains is the QEMU build identity.
- The measurement adapter builds each GPU's `pci-bar-stub` from the captured device rather than a
  hardcoded per-profile table. OVMF sizes the guest's 64-bit MMIO aperture from the BARs it
  enumerates, so the host is the authoritative source by construction; a GPU model with no table
  entry previously could not be measured at all. Table entries remain as a fallback for hosts
  registered before the capture shipped.
- Renamed `measurement/platform_tables.py` to `measurement/image_config.py` and `host/profiles.py`
  to `host/recipes.py`, each in its own commit. `platform_tables` stopped being tables when the
  data moved to the host profile; what the module produces is the tdx-measure image config.
- Removed `guest/command.py`, `measurement/topology_spec.py` and `guest/gpu/topology.py`, and cut
  `guest/detection.py` to the detectors still called. Each held a second way to describe a host
  that `HostProfile` now covers.

### Removed

- The per-GPU `opt/ovmf/X-PciMmio64Mb<N>` fw_cfg hint and its `bar_size_mb` plumbing. The
  firmware reads a single unsuffixed key, so the suffixed ones were never matched — `strings` on
  the shipped OVMF confirms it. OVMF auto-sizes the 64-bit window from the passed-through BARs.
  Every baselined RTMR0 is byte-identical with the code removed.

### Fixed

- Offline generation built NVSwitch and InfiniBand root ports for devices the launcher does not
  attach — on a B300, 14 `rp_ib` ports that never exist in a real boot — because it took the raw
  NUMA vectors while the launch path gated on the GPU profile's `passthrough` entries. Both paths
  now gate identically, so a generated RTMR0 can match the boot it describes.
