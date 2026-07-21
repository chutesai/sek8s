### Added

- `make publish-guest` / `make publish-guest-debug`: upload a built guest image
  **and its direct-boot artifacts** to R2 in one step (via
  `guest-tools/scripts/publish-image.sh` + rclone), replacing the manual
  per-file `rclone copyto`. Uploads `<version>[-debug].{qcow2,vmlinuz,initrd,cmdline}`
  to the canonical `tdx-guest[-debug].{qcow2,vmlinuz,initrd,cmdline}` R2 objects
  that `quick-launch --download` fetches. Pre-flight fails if any of the four is
  missing, so a qcow2 is never published without its matching boot artifacts.
  Prompts once for the rclone config password (`RCLONE_CONFIG_PASS`).
