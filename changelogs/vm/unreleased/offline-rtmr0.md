### Added
- **Fully offline RTMR0 generation.** The `tdx-measure` fork now self-generates the
  complete 15-event RTMR0 for each topology — firmware (MRTD/CFV/secure-boot), the
  QEMU-generated ACPI (loader/rsdp/tables), the `etc/extra-pci-roots`/BootMenu/bootorder
  fw_cfg events, and the SMBIOS handoff — with **no captured CCEL and no TDX hardware**.
  `measurements=offline` now yields COMPLETE measurements on any x86-64 host; the
  `capture-ccel` step is retained only as a `measurements=full` cross-validation against a
  real quote, not a build dependency.
- **Cross-host measurement determinism via the topology fingerprint.** RTMR0 is the only
  CPU-dependent measurement, and only two things move it: the guest vendor drives QEMU's
  SRAT memory-map (AMD guests get a 1 TiB memory hole) and the CPUID leaf-1 becomes the
  SMBIOS Type-4 Processor ID. Offline measurement generation pins the fingerprint's
  `cpu_vendor` into the measurement `-cpu` and patches `cpu_processor_id` into the dumped
  SMBIOS via the fork, so any host — including non-Intel — regenerates the exact production
  RTMR0. (phys-bits was measured to not affect RTMR0 and is not carried.) Launch keeps
  plain `-cpu host` (real silicon, features, transparency).
- The launcher **refuses to boot a host whose fingerprint isn't in the profile's baselined
  set** (exact match on CPU + mem + device layout) — one check that subsumes the former
  separate CPU-identity guard; an unbaselined host's RTMR0 would diverge and never attest.
  A profile whose fingerprint is a placeholder (`cpu_processor_id=None`, pending a
  discover-profile.sh capture) never matches a live host, so it is refused until captured.

### Changed
- **Host-instance facts moved from `GpuProfile` into the topology fingerprint**, which is
  now `TopologyFingerprint(cpu: CpuTopology, mem_gb: int, gpu: GpuTopology)` — the three
  host axes that move RTMR0. `CpuTopology` carries the guest `-smp` (vcpus + sockets) and
  CPU identity (vendor + Processor ID); `mem_gb` is guest RAM; `NumaTopology`/`FlatTopology`
  carry only the device layout. These are derived from the LIVE host at detection (`vcpus =
  host_cpus − host_reserved_cpus`; mem via a per-profile `guest_mem_gb` rule; CPU via
  `/proc/cpuinfo`), and the known values live in `gpu/known_topologies.py` so profiles just
  import them. A `GpuProfile` now holds only
  GPU-model policy plus `host_reserved_cpus` / `guest_mem_gb`. Consequently **`B200Profile`
  and `B200Xeon6Profile` collapse into one `B200Profile`** — the 192-CPU Xeon and 288-CPU
  Xeon 6 hosts are two fingerprints, not two profiles — and an off-nominal host (e.g. a
  192-CPU H200) now derives its own fingerprint/measurement instead of being pinned to the
  nominal one. Published measurement names gain the host shape, e.g.
  `8xb200 [10.2.1, numa-176c-1944g]`.
- `compute-rtmr0` no longer requires a baseline CCEL; it runs the fork to self-generate
  RTMR0 directly. `generate_measurements.py generate` drops the CCEL splice and folds the
  fork's own rtmr0; `--baseline` is now an accepted-but-ignored deprecated flag.
- `discover-profile.sh` additionally reports the host CPU identity (`cpu_vendor`,
  `cpu_processor_id`), computed host-side with no TDX and no guest capture — the Processor
  ID is CPUID leaf-1 (EAX from family/model/stepping, EDX = the TDX Module's fixed leaf-1
  baseline). These are declared once per host class in a profile's fingerprint; a
  fingerprint with `cpu_processor_id=None` stays launch-gated but refuses offline generation
  rather than silently emitting a measurement for the generating host's CPU.
