### Added

- B200 GPU support: host-side Fabric Manager setup in `chutes.host.setup` — detects B200 GPUs at runtime and installs `nvidia-fabricmanager`, `nvlsm`, `libibumad3`, `infiniband-diags`, configures `ib_umad` autoload and `PARTITION_RAIL_POLICY=1` in `fabricmanager.cfg`, and enables `nvidia-fabricmanager.service`.
- `detect_cx7_bridge_pfs()` in `chutes.guest.detection` — identifies ConnectX-7 NVSwitch bridge PFs via VPD `SMDL=SW_MNG` field so they can be excluded from guest passthrough.
- CX7 NVSwitch bridge PF exclusion in `setup_passthrough()` — bridge PFs are logged and excluded before IB NIC VFs are created, preventing accidental VFIO binding of FM-managed devices.
- `(25.10, B200, 8)` added to validated topology matrix in `support_matrix.py`.

### Changed

- `B200Profile` corrected: `host_cpus=192`, `host_sockets=2`, `bar_size_mb=262144` (256 GiB BAR confirmed from hardware).
- `detect_infiniband_pfs()` now accepts an optional `exclude_bdfs` parameter.
