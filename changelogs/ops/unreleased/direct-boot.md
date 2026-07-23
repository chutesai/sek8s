### Added

- `make publish-guest` / `make publish-guest-debug` — upload a built guest image **and
  its direct-boot artifacts** to R2 in one step (via `publish-image.sh` + rclone),
  replacing the manual per-file `rclone copyto`. Uploads
  `<version>[-debug].{qcow2,vmlinuz,initrd,cmdline}` to the canonical
  `tdx-guest[-debug].{qcow2,vmlinuz,initrd,cmdline}` R2 objects that
  `quick-launch --download` fetches; pre-flight fails if any of the four is missing, so a
  qcow2 is never published without its matching boot artifacts. Prompts once for the
  rclone config password (`RCLONE_CONFIG_PASS`).

### Changed

- The TDX launcher now **direct-boots** the guest (1.4.0+, required — no GRUB fallback):
  OVMF boots the image's kernel/initrd directly via QEMU `-kernel`/`-initrd`/`-append`
  instead of GRUB, dropping GRUB/shim from the measured boot chain (TCB reduction).
  `build_base_cmd` always emits the direct-boot args and drops `bootindex` from the disk
  device — the qcow2 stays attached as the LUKS root, just not the boot device. There is
  deliberately no GRUB path: a second boot method would produce a second,
  network-inconsistent set of measurements. The offline ACPI-dump path passes placeholders
  (RTMR0 is boot-method independent).
- Direct-boot artifacts (`<image>.vmlinuz` / `.initrd` / `.cmdline`) are produced once at
  build time and published to R2 alongside the qcow2. `quick-launch --download` /
  `--download-debug` fetch them next to the image, and `chutes.guest.direct_boot` resolves
  them at launch — no per-launch extraction and no `guestfish` on fleet hosts. The launcher
  and the build read the *same* staged files, so the pinned RTMR1/2 match the running VM.
