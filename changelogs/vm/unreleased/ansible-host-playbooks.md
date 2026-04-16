### Added
- Operational Ansible under `ansible/host/` (setup, launch, upgrade) for bare-metal TDX hosts.
- QEMU duplicate-instance guard in `quick-launch.sh`.

### Changed
- Renamed guest image build Ansible directory from `ansible/k3s/` to `ansible/guest/`; VM `VERSION` path is now `ansible/guest/VERSION`.
