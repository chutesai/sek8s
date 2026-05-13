### Added
- `ansible.cfg`: `profile_tasks` callback enabled so every Ansible task now emits timing data — makes it easier to spot slow steps in long playbooks
- `build-setup.yml`: installs Ansible and its Python/system prerequisites on the build host before subsequent plays run, replacing the assumption that Ansible is pre-installed

### Changed
- `upgrade-guest.yml` / `launch_and_verify`: checksum validation now runs as a pre-flight step before the VM is launched and again after guest image promotion, catching stale images earlier and surfacing a clear failure message
- `pre_2510.yml`: adds explicit gating so SGX/DCAP packages that were updated via `unattended-upgrades` are preserved rather than purged during the 25.04 → 25.10 hop; only packages that were not updated get removed

### Fixed
- `pre_2504.yml`: `grub-common` is now pinned/held before the OS upgrade begins to prevent apt from purging it during dependency resolution — loss of `grub-common` left hosts unbootable
- `pre_2504.yml` / `hop.yml` / `post_2504.yml`: corrected task ordering so DKMS kernel module rebuilds and `grub-pc` reconfiguration occur in the right sequence for both fresh 25.04 installs and the 25.04 → 25.10 hop, eliminating boot failures caused by stale module state
