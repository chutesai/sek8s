### Added
- **`B300_256` GPU profile** — B300 on a 2×64c×2t host (256 logical CPUs, ~2 TB
  RAM, 4 NUMA nodes). A submitted host profile of this class (Wistron XD690 CPU
  sled, BIOS 3.1.07, Ubuntu 26.04 / QEMU 10.2.1, 2010 GB RAM, 8× B300 SXM6 AC)
  could not launch under the existing `B300` profile: guest RAM is fixed per
  profile for measurement determinism, and `B300` inherits `ram_per_gpu_gb` from
  `vram_gb = 288` → a 2304G guest, which a 2010 GB host cannot back, so `run-td`
  aborted with *"needs 2304G guest RAM, but only 1946G can be safely backed"*.
  The new profile is a `B300Profile` sibling with `ram_per_gpu_gb = 243` (guest
  RAM 1944G) and `host_cpus = 256` → 252 vcpus
  (`252,sockets=2,cores=126,threads=1`); the base `B300`'s 192 CPUs would have
  stranded 68 logical CPUs on this host. All GPU and passthrough policy — CC
  mode, no NVSwitch/IB passthrough, no per-GPU OVMF MMIO fw_cfg hints, no guest
  NUMA, Fabric Manager requirement — is inherited from `B300Profile` unchanged.
  Device ID `3182` is now shared by two profiles and is disambiguated by exact
  host CPU count (192 → `B300`, 256 → `B300_256`), so a B300 host with any other
  CPU count now raises *"Add a new profile for this CPU topology"* instead of
  silently using the 192-CPU profile. `baselined_measurements` is intentionally
  left empty: the 1944G/252-vcpu guest moves RTMR0 and no measurement is
  registered for this host class yet. An empty map skips the launch-time
  topology hard-match (so the host launches) while `verify-host` still returns
  WARNING rather than claiming a baseline that does not exist — which also means
  `upgrade-host.yml`'s pre-flight will abort on this host until the measurement
  lands (override with `upgrade_preflight_override=true`). Populate it with
  `{"10.2.1": {FlatTopology(gpu_count=8)}}` once the RTMR0 for this profile is
  registered in chutes-ops `teeMeasurements` (B300 never uses guest NUMA, so the
  fingerprint is flat regardless of the host's NUMA node count).
