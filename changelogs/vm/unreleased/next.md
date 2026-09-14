### Fixed

- `sudo` could not run at all from system-manager under enforce, so every privileged path it
  fronts was dead: graceful shutdown, disk usage, and cache reclaim. A production guest reported
  `unable to open /etc/sudo.conf`, `unable to change to root gid` and `error initializing audit
  plugin sudoers_audit` — the config reads were missing from the profile, and so were the
  capabilities. sudo is setuid-root, so the kernel already holds those in root's permitted set,
  but AppArmor mediates capability use per profile and they still have to be declared. Debug
  builds load the profile in complain mode, which allows all of it, so this only ever showed up
  in production. The partial sudo support already present got it as far as loading its plugins,
  which is why it looked configured.
