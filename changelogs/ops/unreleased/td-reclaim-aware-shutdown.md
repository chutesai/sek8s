### Fixed

- `shutdown_via_miner.yml` declared the guest gone while a QEMU still held its image, then
  walked into the qcow2 write-lock collision its own comment warns about. It waited on
  `is_live_chutes_td.sh`, which answers "is a guest *serving*?" — and a QEMU that has powered
  its guest off but is still reclaiming the TD's private memory answers no, while holding the
  image's write lock and every passthrough device for as long as that runs (hours, for a guest
  that had faulted in a terabyte). The wait now asks `chutes-cvm host devices-free`, which
  answers the question that actually matters here: has QEMU let go?

  The escalation had the same blind spot: `stop_chutes_td.sh` reported "No chutes-td QEMU
  process running", exit 0, in precisely the state it was written to catch — so the
  "could not be stopped" failure never fired. It is replaced by `chutes-cvm guest stop --force`,
  which escalates SIGTERM → SIGKILL, verifies the process is gone, and distinguishes a kill
  that failed from a reclaim that simply has not finished. `stop_chutes_td.sh` is deleted; its
  detection logic was one of four divergent copies.

### Changed

- `launch_and_verify.yml` no longer probes for a wedged PCI subsystem before launching, and no
  longer force-reboots on its own. `chutes-cvm guest launch` now checks for itself whether
  anything holds the passthrough devices and refuses with the reason, an ETA when a previous TD
  is still reclaiming its memory, and the SysRq sequence. The probe was a second, worse-informed
  copy of that answer, and it reacted by rebooting — throwing away a reclaim that would have
  finished on its own.

  A refused launch now stops the play with whatever the launch reported. Rebooting instead is
  opt-in via `tee_force_reboot_on_launch_failure` (default false), because whether to spend a
  reboot or wait for a reclaim depends on how long it has left, which the operator can now see.

- `is_live_chutes_td.sh` documents what it answers and what it does not. Skipping zombies is
  correct for its remaining callers — `drain_and_shutdown.yml` and `assert_not_running.yml`,
  which gate pod-drain and skip-on-rerun logic, and must not try to drain a guest that is
  already off. The header now says to call `chutes-cvm host devices-free` for the other
  question rather than widening this script, which is how it came to be used for both.
