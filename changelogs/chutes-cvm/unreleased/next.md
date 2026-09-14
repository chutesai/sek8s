### Fixed

- `B300` could not launch on a host with less RAM than its aggregate VRAM. Guest RAM was
  pinned to `vram_gb * gpus` (2304G for 8×288 GB) regardless of the host, so a ~2 TB sled
  aborted with *"needs 2304G guest RAM, but only 1946G can be safely backed"*. Guest RAM is
  now clamped to what the host can actually back — `min(vram * gpus, ((host_gb - 64) // gpus)
  * gpus)` — which yields 1944G on a 2010 GB host. The clamp is deliberate: a host with
  enough RAM still gets exactly 2304G, so its fingerprint `mem_gb`, and therefore RTMR0, is
  unchanged and no in-service B300 is re-baselined. Such a host is a second fingerprint of
  the same profile rather than a sibling class — vcpus and memory are detected from the live
  host — so a 256-CPU/2 TB sled needs no new profile, only its own RTMR0 baseline registered
  in chutes-ops `teeMeasurements`.
