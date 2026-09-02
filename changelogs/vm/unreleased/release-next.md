### Fixed

- Production guests no longer power off during k3s cluster init. `03-k3s-validator-auth.sh` read
  `/run/chutes/validator-ss58` directly, but cluster-init scripts are launched as `bash <script>` —
  exec'ing `/usr/bin/bash` by name, which `@{confined_bins}` auto-attaches to
  `sek8s.deny-sensitive-default`, whose `sek8s-secrets-deny` abstraction denies `/run/chutes/**`.
  The read returned EACCES, the script exited non-zero, and the wrapper's fatal handler powered the
  VM off. The `k3s-post-start.sh` wrapper runs unconfined, so it now reads the value and exports
  `VALIDATOR_SS58` to the init scripts, crossing the profile boundary the filesystem cannot.
- `system-manager` no longer fails to start in production. `/var/snap/cache/.xdg-cache` was created
  by a `+`-prefixed `ExecStartPre` in the `cache-volume.conf` drop-in; the `+` prefix makes systemd
  skip `AppArmorProfile=`, so `/bin/bash` auto-attached `sek8s.deny-sensitive-default` and was
  denied `/var/snap/cache/**`. The directory is now created by `setup-cache.sh`, which owns the
  cache-volume layout and already runs as root under `sek8s.setup-cache`.

Both failures were invisible on debug images, which load the sek8s profiles in complain mode.

### Changed

- `system-manager`'s uid/gid are pinned to the literal `10150` instead of
  `{{ system_manager_uid | default(10150) }}`. Nothing ever defined those variables, and
  `setup-cache.sh` now chowns the XDG cache dir numerically, so the value must not vary.
