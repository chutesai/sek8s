### Fixed

- A launch following a VM that had run a GPU workload could leave the host unable to reboot.
  When a TD powers off, QEMU does not exit: its last thread stays in `do_exit` releasing the
  guest_memfd that backs the TD's private memory, and KVM issues a SEAMCALL sequence per private
  page to block, track, cache-write-back and reclaim it. Measured on an 8xH200 host: ~39us per 4KB
  page, single-threaded, ~25.7k pages/s — a guest that had faulted in ~714GB left 187M pages and
  just over two hours of work. Throughout, that process still holds every passthrough device's
  vfio file descriptor, so `guest launch`'s stale-device unbind blocked in uninterruptible D state;
  and once those blocked unbinds exist, `reboot` hangs in `device_shutdown()` too, turning a
  teardown that would have completed on its own into a host needing a SysRq reset.

  The existing `pci_operations_wedged()` check could not prevent this: it looks for D-state tasks,
  which only exist *because* an earlier launch already fired the unbinds, so it protected the
  second attempt and never the first. `guest/vm.py` — which already owned the process — now also finds a previous one
  (`find_qemu_process`, matching `comm` and per-thread state), reads how far its reclaim has
  got (`read_reclaim`, from KVM's `pages_4k` counter), and answers whether anything still holds the
  passthrough devices (`device_blockers()`; empty means they are free). The gate fires before
  `bind_passthrough` touches anything — SR-IOV VF creation as much as the unbind — and `guest
  launch` now refuses with the remaining pages, the drain rate, an ETA, and the two ways forward:
  wait for it to finish, or reset while a reboot still works. Not force-overridable, because
  forcing past it costs a host reboot rather than a failed command.

  Detection reads `comm` and per-thread state, never `cmdline`: a zombie's
  `/proc/<pid>/cmdline` is empty, which is why every pgrep-style check reported the host as idle
  while it still held all 12 devices. A zombie whose threads have all exited has already released
  its file table and is correctly ignored. Progress comes from KVM's `pages_4k` counter under
  `/sys/kernel/debug/kvm/<qemu-pid>-<vm-fd>/`, sampled twice; it is root-only and best-effort, so
  the refusal still works without it, just without an ETA.

  Not fixable in this package: the cost tracks S-EPT *entry count*, not bytes (4KB per 39us is
  ~105MB/s against hardware good for tens of GB/s, so it is nearly all fixed per-entry overhead),
  and guest_memfd has no hugepage support on these kernels — `CONFIG_KVM_GUEST_MEMFD=y` with no
  hugepage symbol, and `-object tdx-guest` exposes no options. 2MB backing would cut the entry
  count ~512x; until it exists upstream, detecting the state and naming the escape is the fix.

### Changed

- The `pci_operations_wedged()` error, which is now only reachable when an earlier run already
  created the blocked unbinds, no longer says to reboot the host. A plain `reboot` hangs in
  `device_shutdown()` while those tasks exist — the advice actively made things worse. It now
  explains that the tasks clear on their own once reclaim finishes, and gives the SysRq sequence
  for resetting immediately.

- Four separate detectors answered "is a chutes-td QEMU running?", all by matching
  `/proc/<pid>/cmdline`, and all deliberately skipping zombies — so every one of them reported
  the host as idle during a reclaim, when that process holds all twelve devices.
  `launch.py`'s `_chutes_td_running()` is gone; the duplicate-VM gate now uses the same
  `comm`-based scan as the teardown gate, which also fixes it: it previously let a second VM
  launch while the first was still reclaiming. The two refusals keep separate policy — a VM
  that is merely running stays `--force`-overridable, while one mid-reclaim is not, because
  forcing past that reaches the unbind.

  Still outstanding: `scripts/devices/reset-gpus.sh` and the two `ansible/host/.../*_chutes_td.sh`
  helpers carry the same cmdline-and-skip-zombies logic. `reset-gpus.sh` is the worst of them —
  during a reclaim it would SBR-reset GPUs a live QEMU still holds. Their comments also still
  point at the now-deleted `launch.py (_chutes_td_running)`.

- `"chutes-td"` had three definitions (`guest/vm.py`, `guest/launch.py`, and the new detector),
  with `launch.py` both importing it from `vm.py` and redefining it. `guest/vm.py` is now the
  single owner of `PROCESS_NAME`, `PIDFILE` and `LOGFILE`.

- The launch precondition moved out of `bind_passthrough` and up into `launch_vm`, immediately
  before it. Binding devices is not the place to decide whether a launch may happen, and having
  it there created a `vm -> passthrough -> vm` import cycle that was the only reason the
  detection needed modules of its own.

- `scripts/devices/reset-gpus.sh` no longer carries its own copy of the detection. Its whole
  purpose is to refuse while the GPUs are in use and then SBR-reset them, and its `cmdline`
  match plus explicit zombie skip meant that during a reclaim it saw no VM and went ahead —
  SBR-resetting GPUs a live QEMU still held, which is the worst thing it can do. It now calls
  `chutes-cvm host launch-safe`, and fails closed with an honest message if the CLI is absent.

### Added

- **`chutes-cvm host devices-free`** — exits 0 if nothing holds this host's passthrough
  devices, 1 otherwise, printing each reason. The same `device_blockers()` a launch checks,
  exposed because the callers that need it are not all launches: `host reset-gpus` must not SBR
  devices a QEMU still holds, and host Ansible must not open the guest image while one holds
  its write lock. Note the exit sense is the conventional one (0 means OK), unlike
  `vfio-wedged`, which exits 0 when it *finds* a problem; matching that here would make every
  `if` around it read backwards.

  The checks behind it stay individually callable, because which one applies is
  context-dependent: "does anything hold the devices?" (a running *or* reclaiming QEMU) is
  what a launch, a rebind and an SBR need, while "is a guest *serving*?" — where a reclaiming
  QEMU counts as no — is the right question for deciding whether to drain pods or skip a
  shutdown step.

- A `pages_4k` reading of 0 was reported as "the counter is not draining -- treat it as
  stalled". Verified on an 8x RTX PRO 6000 SNP host, that is the normal case there and the
  advice was backwards: SNP zeroes the counter the moment the guest powers off and then hands
  the memory back over the next few minutes, invisible to it. An operator was being told to
  reset a host with three minutes left. Zero now reads as "this counter cannot tell you",
  with the advice to poll `host devices-free`; "stalled" is reserved for pages that remain
  and do not move. `read_reclaim` also returns immediately on a zero reading instead of
  sleeping out its sample window, which a polling caller would otherwise pay per iteration.

  Measured for the record: SNP reclaimed 465GB in 4m38s (~1.7GB/s), roughly 25x faster per
  byte than TDX, so the hours-long case is TDX-specific. The state itself — a zombie leader
  with one live thread holding every device — is not, and the detection is verified on both.

