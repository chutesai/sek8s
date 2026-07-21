### Fixed

- Debug guest images (`debug_build: true`) shipped key-only: the debug-credentials
  play edited the main `sshd_config`, but Ubuntu's `sshd_config.d/50-cloud-init.conf`
  drop-in (`PasswordAuthentication no`) is Included first and wins first-match
  precedence, so password/console access never took effect — making the image
  unusable without the build-time SSH key. The play now writes a `00-debug-access.conf`
  drop-in that sorts ahead of the cloud-init one, restoring root password SSH login
  for debug builds.
