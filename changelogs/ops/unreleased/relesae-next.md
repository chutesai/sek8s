### Added

- `make guest-requirements` regenerates the hash-pinned requirements for the guest venv from
  `poetry.lock`, and `make check-guest-requirements` fails if the committed file is stale.
  Run the former after any dependency change to `sek8s` or `sek8s-common`, or the built image
  installs different packages than the lock describes.
- A `guest-requirements` CI job gates both directions of that drift: `poetry check --lock` for a
  dependency edited without re-locking, and `make check-guest-requirements` for a lock change
  without regenerating. Without it a stale file could reach `main` and publish an image whose
  contents the repo does not describe — which nobody can detect afterwards, and which leaves a
  third party unable to reproduce our measurements.

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
