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
