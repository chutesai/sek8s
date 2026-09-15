### Changed

- `upgrade-guest.yml` now runs `chutes-cvm host setup --noninteractive` on every upgrade,
  after the needs-upgrade gate and before the download. Host config converges to what the
  target release expects instead of only the guest image moving — a host provisioned by an
  older release was missing whatever later folded into `host setup`. Up-to-date hosts still
  end before it, and a host-config failure now aborts with the VM still serving.
- `publish-image.sh` uploads with `--s3-upload-concurrency 16` (rclone's default is 4).
  `--transfers` is per-file and only the qcow2 is large, so parallel part uploads are what
  set publish time. Override with `RCLONE_UPLOAD_CONCURRENCY`.

### Fixed

- `upgrade-guest.yml` skipped its dated backup when upgrading off a pre-1.4.0 host: it
  stats the image-set directory, but those hosts have a single qcow2 file at
  `<default>.qcow2`. The one upgrade that most needs a rollback point was the one that
  silently left the old image orphaned. Both layouts are now backed up.
- `build-setup.yml` now installs `rclone`, which `make publish-guest` shells out to.
  A fresh build host previously reached the publish step without it and failed there,
  after the image build had already completed.
