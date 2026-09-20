### Fixed

- RTMR3 no longer drifts after a VM's first boot. `nvidia-persistenced-config.service`
  generated its systemd drop-in at boot under `/etc/systemd/system`, which 1.4.0 added to
  the RTMR3 measured path list. The boot-time gate only hash-checks paths already in the
  build manifest, so the new file passed unnoticed while still changing the chain digest:
  every VM attested its registered RTMR3 on the first boot and a different value on every
  boot after, in two fleet-wide variants depending on whether NVSwitch was detected. The
  drop-in is now static and baked into the image, and only the persistence flag varies,
  passed as `NVPD_FLAG` through an env file under `/run` (tmpfs, outside the measured
  set). NVSwitch and non-NVSwitch hosts now measure identical bytes and share one RTMR3.
- A failed boot no longer drops to an interactive root shell on the guest console. The
  kernel cmdline now carries `panic=0`, which stops the initramfs `panic()` handler from
  spawning one (and selects halt over a reboot loop). The existing `systemd.mask=` console
  hardening could not cover this: systemd never runs when the initramfs cannot mount root.
  The initramfs is writable in RAM and RTMR1 covers it only as loaded, so a shell there
  could have neutered the RTMR3 measurer before letting the boot continue.
- `fetch_key_and_unlock` is fail-closed from its first line. Its config validation could
  exit before the cleanup trap was installed, and `run_scripts` does not abort the boot on
  a non-zero init-premount script — so that exit fell through to `mountroot` with no root
  device instead of powering the VM off. An early trap now covers the whole script,
  including a failure to source its own helpers. The debug image remains deliberately
  fail-open.

### Changed

- **Boot measurements change in this release.** `panic=0` is part of the direct-boot
  kernel cmdline, so images built from 1.4.1 must be re-registered; the 1.4.0 values will
  not match.
