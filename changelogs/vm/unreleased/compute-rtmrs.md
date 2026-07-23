### Added

- `compute-rtmrs` build step: `chutes-miner-vm.yml` now computes all expected
  build-time RTMRs (1, 2, 3) from the finalized image before LUKS encryption in a
  single step. The new `compute-rtmrs` role composes `stage-boot-artifacts`,
  `compute-rtmr3` (unchanged), `tdx-measure` (provisioning), and `compute-rtmr1-2`.
- `stage-boot-artifacts` role: extracts
  the direct-boot kernel/initrd/cmdline from the finalized image once and persists
  them next to it as `<image>.vmlinuz` / `.initrd` / `.cmdline` — published to R2
  with the qcow2 and read by both `compute-rtmr1-2` (build) and the launcher (deploy).
- `compute-rtmr1-2`: computes RTMR1/RTMR2 from the staged artifacts via the
  `virtee/tdx-measure` fork in `--runtime-only` direct-boot mode. Emits
  `<image>.rtmr1` / `<image>.rtmr2` (bare uppercase hex, like `<image>.rtmr3`).
  These are version-level (topology-independent) and must come from the prod
  image — the debug image's initrd differs, so its RTMR2 would be wrong.
- `tdx-measure` role: provisions the fork binary on the build host (clones
  `chutesai/tdx-measure` if absent — an existing checkout is reused untouched —
  and `cargo build --release`s it), so the build is self-contained. Override
  `tdx_measure_bin` to use a prebuilt binary and skip cloning/building. Build-host
  prereqs: `git`, `cargo`, and `libguestfs-tools` (guestfish).

- `compute-rtmrs` now ensures build-host prerequisites itself (`libguestfs-tools`,
  `git`, and — only when the build user has no toolchain — `cargo`), so the build no
  longer fails late on a missing host dependency.
