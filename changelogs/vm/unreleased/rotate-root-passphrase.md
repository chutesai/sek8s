### Added

- `ansible/guest/roles/luks/files/initramfs/luks-helpers`: shared initramfs shell library with `write_key_file`, `shred_key_file`, `luks_add_key`, `luks_remove_key` — sourced by both `fetch_key_and_unlock` (init-premount) and `setup_storage` (init-bottom).
- Root LUKS passphrase rotation in `fetch_key_and_unlock` (init-premount): detects first-boot LUKS2 token (id 15, type `chutes-first-boot`), sends `first_boot` flag in boot attestation POST, enforces mandatory rotation on every boot — adds new key slot, confirms with API, then kills all pre-existing slots by number to ensure no stale keys remain on the device.

### Changed

- `ansible/guest/roles/luks/tasks/luks_encrypt.yml`: added `type: luks2` to the LUKS container creation task (previously relied on cryptsetup default); added first-boot LUKS2 token task (`chutes-first-boot`, id 15) after container creation; added task to copy shared `luks-helpers` script into the initramfs.
- `ansible/guest/roles/luks/files/initramfs/fetch_key_and_unlock`: updated boot attestation POST body to include `first_boot` flag; added slot enumeration and `luksKillSlot`-based cleanup after successful rotation confirm; rotation confirm failure now rolls back cleanly and powers off; any key slot cleanup failure powers off rather than proceeding with stale slots.
- `ansible/guest/roles/luks/files/initramfs/setup_storage`: extracted LUKS helpers to shared `luks-helpers` file; `finalize_rotation` now uses `luksKillSlot` by slot number (cleaning up stale slots from prior incomplete rotations); any slot cleanup or rollback failure powers off.
