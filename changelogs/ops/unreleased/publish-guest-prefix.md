### Added

- `publish-image.sh` takes `--prefix <dir>` and `--name <basename>`, exposed as
  `make publish-guest PREFIX=… NAME=… FORCE=1`. `PREFIX=tdx-guest-1.4.0` archives a whole
  image set as one bucket entry (`tdx-guest-1.4.0/tdx-guest.*`) instead of five loose
  objects, keeping the canonical names inside so a restore is a copy back to the root. The
  manifest records artifacts by role, not filename, so an archived set verifies unchanged.
  Either option makes the publish archival, which refuses to overwrite an existing set
  unless `FORCE=1`; publishing under the default name still overwrites, as the fleet
  serves it.

### Fixed

- The rclone config password prompt reads with `-r` (a backslash in the password was
  consumed as an escape) and verifies the password before starting a multi-GB upload.
  A wrong one previously surfaced as rclone's own fallback prompt, once per artifact,
  partway through publishing.
