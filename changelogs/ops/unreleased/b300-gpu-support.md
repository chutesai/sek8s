### Added
- B300 Blackwell HGX GPU support for TDX VM launch (`B300Profile`: PCI device ID `3182`, 288 GiB HBM3e VRAM, 2-socket/192-vCPU topology).
- Per-profile TDVF firmware selection (`firmware_filename` property).
- `use_ovmf_mmio_fw_cfg` profile property — B300 disables per-GPU fw_cfg MMIO hints in favour of OVMF auto-sized multi-TB MMIO window.
- `get_sbr_reset_args()` profile method for Secondary Bus Reset recovery after CC-mode switch.
- PCI wedge detection (`pci_operations_wedged()`, `wait_pci_operations_idle()`) — pre-flight and post-unbind checks abort with a clear message instead of hanging when the PCI subsystem is stuck in D-state.

### Changed
- All GPU profiles now use `OVMF.inteltdx.ms.fd` firmware (Ubuntu `ovmf-inteltdx 2025.11-3ubuntu7`). Addresses CVE-2025-2296 (legacy Linux loader disabled in TDX guests). Old `TDVF.fd` removed.
- `gpu-admin-tools` bumped to v2026.06.05 with hardened B300 PCI recovery.
- B300 disables InfiniBand passthrough: all ConnectX-7 IB-class PFs are NVSwitch bridge devices managed by host-side Fabric Manager; guest networking uses virtio-net.
