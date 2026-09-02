### Added

- **`B200_XEON6_256` GPU profile** — B200 on a 2×64c×2t Xeon 6 host (256 logical
  CPUs, ~3 TB RAM, SNC off → 2 NUMA nodes, GPUs 4+4). A submitted host profile of
  this class (KR9288-X3 board, Ubuntu 26.04 / QEMU 10.2.1) matched neither existing
  B200 sibling — device ID `2901` is disambiguated by exact host CPU count, and the
  registry only knew 192 (`B200`) and 288 (`B200_XEON6`) — so `_match_gpu_model`
  raised *"Add a new profile for this CPU topology"* and the host could not launch
  at all. The new profile is a third `B200Profile` sibling with `host_cpus = 256`:
  240 vcpus (`240,sockets=2,cores=120,threads=1`), and the same
  `ram_per_gpu_gb = 369` (guest RAM 2952G) as `B200_XEON6`. All GPU and passthrough
  policy — CC mode, no NVSwitch/IB passthrough, guest NUMA, post-launch tuning,
  Fabric Manager requirement — is inherited from `B200Profile` unchanged.

  `baselined_measurements` is intentionally left empty: the 240-vcpu `-smp` moves
  RTMR0 and no measurement is registered for this host class yet. An empty map
  skips the launch-time topology hard-match (so the host launches) while
  `verify-host` still returns WARNING rather than claiming a baseline that does not
  exist — which also means `upgrade-host.yml`'s pre-flight will abort on this host
  until the measurement lands (override with `upgrade_preflight_override=true`).
  Populate it with `{"10.2.1": {NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1))}}`
  once the RTMR0 for this profile is registered in chutes-ops `teeMeasurements`.
