### Changed

- `discover-profile.sh` emits per-device lists — `gpus`, `nvswitches`, `ib_devices` — each object
  carrying its own BDF, vendor/device/class, NUMA node and BAR layout. Previously the document
  described a platform through parallel arrays (`gpu.bdfs`, `gpu.numa_nodes`, `nic.ib_devices`,
  …) whose entries had to be zipped back together by index at every reader. BAR sizes come from
  world-readable `/sys/bus/pci/devices/*/resource`, so the capture still runs unprivileged and
  works with the GPUs already bound to `vfio-pci`. The parallel-array blocks they replace (`gpu`,
  `nic`, `nvswitch`) are gone, along with the array builders that fed them -- they had nowhere to
  put a BAR layout, which is why the device lists exist. `host` and `pci_topology` stay as operator
  diagnostics; neither is submitted.
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
  entry previously could not be measured at all. The per-profile `passthrough` tables are gone
  entirely, along with the fallback that read them: BAR2 is resizable, so two machines of one
  model can differ and only the capture knows which. A device that captured no BARs now fails
  loudly rather than being measured against a shipped constant.
- Renamed `measurement/platform_tables.py` to `measurement/image_config.py` and `host/profiles.py`
  to `host/recipes.py`, each in its own commit. `platform_tables` stopped being tables when the
  data moved to the host profile; what the module produces is the tdx-measure image config.
- Removed `guest/command.py`, `measurement/topology_spec.py` and `guest/gpu/topology.py`, and cut
  `guest/detection.py` to the detectors still called. Each held a second way to describe a host
  that `HostProfile` now covers.

- `HostProfile.to_api_profile()` is what `submit-profile` sends: the RTMR0 determinants and
  nothing else. Stored == hashed == required, one set -- an unhashed field stored beside a hashed
  one re-splits the class at the byte level, so two hosts that measure identically differ in the
  stored row and the API's first-write-wins silently drops one. Host RAM is the worked example:
  2007 GB and 2011 GB are one H200 class, and keying the host total split them. Device host
  addresses go the same way: the measured command swaps every endpoint for a `pci-bar-stub`, so a
  BDF never reaches RTMR0 and only says which slot a card sits in. A stored profile reads back
  into the same `HostProfile`, and generating from one produces a byte-identical RTMR0 to
  generating from the full capture. Profiles shrink from 28-46 KB to 0.9-2.3 KB.
- Guest RAM is resolved in exactly one place -- `HostProfile.from_host()`, when the host is read --
  and carried from there through submission, storage and generation. `guest_mem_gb` is a plain
  read that refuses a document without it rather than re-deriving. The sizing rules are
  per-`GpuProfile`, so a later release deriving a different answer would measure one guest and
  file it under a key computed from another; storage drops the host total anyway, which makes
  re-deriving impossible as well as wrong. A profile predating the current capture now fails
  loudly instead of being measured from a value this release invented.
- The capture's `cpu` block is `count` / `vendor` / `processor_id`, matching `HostCpu`. It said
  `total` / `cpu_vendor` / `cpu_processor_id` while the class said the other thing, so a
  round-tripped profile came back `count=0, processor_id=None` -- a guest with no CPUs, measured
  without complaint. One spelling end to end.
- `submit-profile` sends `HostProfile.to_api_json()` rather than the whole capture. `bdf` stays
  mandatory on a capture -- it binds the device and orders the list -- and is simply not sent;
  `from_api_profile()` supplies positional stand-ins when a stored profile is read back to
  generate its measurement, at the boundary where they are known to be meaningless. `--target-os`
  now rewrites only the QEMU version, since the OS release is no longer submitted.

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
