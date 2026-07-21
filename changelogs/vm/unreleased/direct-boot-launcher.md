### Changed

- TDX launcher now **direct-boots** the guest (1.4.0+, required — no GRUB
  fallback): OVMF boots the image's kernel/initrd directly via QEMU
  `-kernel`/`-initrd`/`-append` instead of GRUB, dropping GRUB/shim from the
  measured boot chain (TCB reduction). `build_base_cmd` now always emits the
  direct-boot args and drops `bootindex` from the disk device — the qcow2 stays
  attached as the LUKS root, just not the boot device. There is deliberately no
  GRUB path: a second boot method would produce a second, network-inconsistent set
  of measurements. The offline ACPI-dump path passes placeholders (RTMR0 is
  boot-method independent and the measured tables exclude the kernel).
- Direct-boot artifacts (`<image>.vmlinuz` / `.initrd` / `.cmdline`) are extracted
  **once at build time** (`stage-boot-artifacts.sh`, pre-encryption) and published
  to R2 alongside the qcow2. `quick-launch --download` / `--download-debug` fetch
  them next to the image; `chutes.guest.direct_boot` resolves them at launch — no
  per-launch extraction, no `guestfish` on fleet hosts. Both `compute-rtmr1-2`
  (build) and the launcher (deploy) read the *same* staged files, so the pinned
  RTMR1/2 match the running VM by construction.
