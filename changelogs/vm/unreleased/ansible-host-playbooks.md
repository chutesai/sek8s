### Added
- Operational Ansible under `ansible/host/` (setup, launch, upgrade) for bare-metal TDX hosts.
- QEMU duplicate-instance guard in `quick-launch.sh`.

### Changed
- Renamed guest image build Ansible directory from `ansible/k3s/` to `ansible/guest/`; VM `VERSION` path is now `ansible/guest/VERSION`.

### Fixed
- `ansible/host` `setup.yml`: `tdx_bootstrap` now always runs `setup-tdx-host` unconditionally instead of falling back to `--install-tools-only` when the TDX module is already initialized. The previous branch skipped all `apt install` steps (including `sgx-dcap-pccs`, `tdx-qgs`, and attestation packages), causing `pccs_configure` to fail with "Could not find the requested service pccs" on hosts where BIOS pre-enables TDX before first boot.
- `ansible/host` `tdx_bootstrap`: reboot detection now also compares `uname -r` against the `saved_entry` in `/boot/grub/grubenv`. Previously only `/var/run/reboot-required` was checked; that file is written by apt's `update-notifier-common` hook and is absent when packages were already installed or the hook package is missing, causing the role to skip the reboot even when GRUB had been updated to a different kernel.
