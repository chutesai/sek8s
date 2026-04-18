### Added
- Operational Ansible under `ansible/host/` (setup, launch, upgrade) for bare-metal TDX hosts.
- QEMU duplicate-instance guard in `quick-launch.sh`.

### Changed
- Renamed guest image build Ansible directory from `ansible/k3s/` to `ansible/guest/`; VM `VERSION` path is now `ansible/guest/VERSION`.

### Fixed
- `ansible/host` `setup.yml`: `tdx_bootstrap` now always runs `setup-tdx-host` unconditionally instead of falling back to `--install-tools-only` when the TDX module is already initialized. The previous branch skipped all `apt install` steps (including `sgx-dcap-pccs`, `tdx-qgs`, and attestation packages), causing `pccs_configure` to fail with "Could not find the requested service pccs" on hosts where BIOS pre-enables TDX before first boot.
