### Added

- `ansible/guest/roles/luks/files/initramfs/luks-helpers`: shared initramfs shell library with `write_key_file`, `shred_key_file`, `luks_add_key`, `luks_remove_key` — sourced by both `fetch_key_and_unlock` (init-premount) and `setup_storage` (init-bottom).
- Root LUKS passphrase rotation in `fetch_key_and_unlock`: detects first-boot LUKS2 token (id 15), includes `first_boot` flag in boot attestation POST, parses `root_next`/`root_confirm_nonce` from response, removes first-boot token after `luksOpen`, and performs mandatory add-confirm-remove rotation on every boot.
- First-boot LUKS2 token (`chutes-first-boot`, id 15) added to root partition at image build time in `luks_encrypt.yml`; carries the `vm_version` as local debug metadata. Signals to the API that this VM is booting from its original published state and should receive the build-time default passphrase.

### Changed

- `ansible/guest/roles/luks/tasks/luks_encrypt.yml`: added `type: luks2` to the "Create LUKS container" task (previously relied on cryptsetup default).
- `host-tools/scripts/prepare-vm-image.sh`: replaced QEMU qcow2 overlay creation with a full `cp` of the base image into a per-VM file; added stale-image cleanup for previous base image versions.
- `host-tools/scripts/quick-launch.sh`: renamed `--overlay-dir` to `--vm-image-dir`; default directory changed from `/var/lib/chutes/vm-overlays/` to `/var/lib/chutes/vm-images/`.
- Config key `overlay_directory` renamed to `vm_image_directory` in schemas, templates, example configs, `config.py`, and `CONFIG-GUIDE.md`.
