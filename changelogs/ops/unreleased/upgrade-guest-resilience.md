### Changed

- `upgrade-guest.yml` no longer hashes the multi-GB base qcow2 to decide whether
  a host needs upgrading. It reads `needs_upgrade` per server from
  `chutes-miner tee maintenance-status --raw-json` and ends already-current hosts
  immediately (no download, no hash), so running with no `--limit` safely walks
  the whole fleet and only touches hosts behind the active window's target
  version. When an upgrade is needed the image is fetched once and verified by
  aria2 itself (`--checksum` against the repo-pinned `EXPECTED_BASE_SHA256`).

### Fixed

- Guest upgrade/remediation no longer marches into a cryptic
  `qemu-nbd: Failed to get "write" lock` when the guest fails to power down.
  `shutdown_via_miner.yml` now escalates SIGTERM→SIGKILL to a stuck `chutes-td`
  QEMU (`stop_chutes_td.sh`) and, if it survives SIGKILL (uninterruptible
  D-state), fails loudly instructing the operator to reboot the host.
- `create-config.sh` now recovers a stale `qemu-nbd` holding the config image
  (leftover from an interrupted run) and retries once, and otherwise reports the
  remaining holders (lsof/fuser) instead of failing with an opaque lock error.
- The force-evict path (`drain_and_shutdown.yml`, `force_upgrade=true`) and
  `shutdown.yml` now auto-confirm the `sync-kubeconfig` "Continue? [y/N]" prompt
  (`stdin: "y\n"`), matching the normal drain path. Previously these aborted with
  `Aborted.` / non-zero return code.
- VM launch (`launch_and_verify.yml`) now guards against a wedged GPU/PCI
  passthrough subsystem (stuck vfio-pci/nvidia-gpu-tools D-state tasks that make
  `run-td` fail with "SBR cannot run until the host is rebooted"). It pre-flight
  detects the wedge via `chutes.guest.vfio.pci_operations_wedged()`, and if the
  launch itself wedges the host it reboots, waits for SSH, and retries the launch
  once. Tunable via `upgrade_reboot_timeout_seconds` (default 1800).
- The wedge-recovery reboot is now a forced kernel-level SysRq reboot
  (`force_reboot.yml`) instead of a graceful one. A graceful reboot hangs
  indefinitely in `systemd-shutdown` waiting for the wedged QEMU and other
  un-killable D-state tasks (seen on the iLO console as "Waiting for process:
  ... chutes-td ..."), leaving the host stuck mid-reboot. SysRq 's' then 'b'
  syncs and reboots immediately, bypassing service/device shutdown.
