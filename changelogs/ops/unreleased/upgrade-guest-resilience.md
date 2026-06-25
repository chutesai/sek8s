### Changed

- `upgrade-guest.yml` now hashes the multi-GB base qcow2 at most once per run
  (a single sha256 of the live image to decide whether an upgrade is needed, with
  a clean no-op when already current so a host that is up to date never
  re-downloads). When an upgrade is needed the image is fetched once and verified
  by aria2 itself (`--checksum`). This removes the old 2-3 redundant `sha256sum`
  passes.

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
  once. Tunable via `upgrade_reboot_timeout_seconds` (default 900).
