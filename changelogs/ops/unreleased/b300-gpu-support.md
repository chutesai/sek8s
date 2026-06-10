### Added
- B300 Blackwell HGX GPU support for TDX VM launch (`B300Profile`: PCI device ID `3182`, 288 GiB HBM3e VRAM, 2-socket/192-vCPU topology).
- Per-profile TDVF firmware selection (`firmware_filename` property); B200 now uses the `OVMF.inteltdx.ms.fd` build from the repo `firmware/` directory.
- `use_ovmf_mmio_fw_cfg` profile property — B300 disables per-GPU fw_cfg MMIO hints in favour of OVMF auto-sized multi-TB MMIO window.
- `get_sbr_reset_args()` profile method for Secondary Bus Reset recovery after CC-mode switch.

### Changed
- `gpu-admin-tools` bumped to v2026.06.05 with hardened B300 PCI recovery.
- B300 disables InfiniBand passthrough: all ConnectX-7 IB-class PFs are NVSwitch bridge devices managed by host-side Fabric Manager; guest networking uses virtio-net.
