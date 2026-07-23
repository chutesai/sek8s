### Added

- **Build-time RTMR computation** — `chutes-miner-vm.yml` now computes all expected
  build-time RTMRs (1, 2, 3) from the finalized image before LUKS encryption, in one
  `compute-rtmrs` role that composes `stage-boot-artifacts`, `compute-rtmr3`,
  `tdx-measure` (fork provisioning), and `compute-rtmr1-2`. Emits `<image>.rtmr1`,
  `.rtmr2`, `.rtmr3` (bare uppercase hex). RTMR1/2 are version-level
  (topology-independent) and come from the prod image — the debug image's initrd
  differs, so its RTMR2 would be wrong. The role ensures its own build-host
  prerequisites (`libguestfs-tools`, `git`, and `cargo` only when the build user has
  none); `tdx-measure` clones/builds the `chutesai/tdx-measure` fork (reusing an
  existing checkout), overridable via `tdx_measure_bin`.
- **`stage-boot-artifacts`** — extracts the direct-boot kernel/initrd/cmdline from the
  finalized image once and persists them next to it as `<image>.vmlinuz`, `.initrd`,
  `.cmdline`. Published to R2 with the qcow2 and read by both `compute-rtmr1-2` (build)
  and the launcher (deploy), so the pinned RTMR1/2 match the running VM by construction.
- **`capture-measurement-baseline.yml`** — a local build-server step that captures the
  offline-measurement baseline (the RTMR0 inputs) from the freshly-built debug image:
  copies it to `/tmp` so the publishable artifact is never mutated, TDX-boots the copy,
  captures the CCEL + fw_cfg ACPI/SMBIOS preimages into the top-level
  `measurements/<version>/`, verifies the CCEL actually landed, and tears down.
- **`guest-tools/measurement/`** — offline RTMR0 measurement/verification tooling:
  `ccel_replay.py` (CC event-log parse + SHA-384 RTMR replay, with a per-register
  `diff`), `capture-measurement-artifacts.sh` (capture the CCEL + preimages),
  `extract-measurements.sh` (report a running guest's live MRTD + RTMR0-3 from a fresh
  quote), and `utils/` (SMBIOS-event preimage matcher, per-table ACPI byte-diff). Reuses
  the launcher's QEMU-arg builders and the `virtee/tdx-measure` fork.
- **`docs/specs/tdx-measurement-verification.md`** — how TDX guest measurements are
  structured, why RTMR0 is the only per-topology register, and how they are
  independently reproduced and verified.

### Changed

- Build-pipeline-only scripts moved from `guest-tools/scripts/` into their Ansible role
  `files/` (invoked exclusively by the build): `compute-rtmr3.sh`, `compute-rtmr1-2.sh`,
  `stage-boot-artifacts.sh`, and `extract-vm-measurements.sh`. `guest-tools/scripts/` now
  holds only the standalone release tool `publish-image.sh`.

### Fixed

- Debug guest images (`debug_build: true`) shipped key-only: the debug-credentials play
  edited the main `sshd_config`, but Ubuntu's `sshd_config.d/50-cloud-init.conf` drop-in
  (`PasswordAuthentication no`) is Included first and won first-match precedence, so
  password/console access never took effect. The play now writes a `00-debug-access.conf`
  drop-in that sorts ahead of the cloud-init one, restoring root password SSH login.

### Removed

- `guest-tools/scripts/extract-acpi.sh` — dead: the old host-side ACPI dump that had to
  be hand-synced with the launcher. Superseded by offline generation that shares the
  launcher's exact `QemuCommand` and generates ACPI via `tdx-measure --create-acpi-tables`.
- `guest-tools/scripts/run-image.sh` — dead, unreferenced libvirt/VNC/cloud-init test-boot
  script predating the current `run-td` flow.
- `guest-tools/README.md` — the old manual step-by-step measurement guide, superseded by
  build-integrated `compute-rtmrs` + the `guest-tools/measurement/` tooling; the concepts
  now live in `docs/specs/tdx-measurement-verification.md`.
