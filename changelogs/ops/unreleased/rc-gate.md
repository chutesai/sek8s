### Added
- Image-set coherence checking, as the single image format. A VM image is a set — the
  qcow2 plus its direct-boot `.vmlinuz`/`.initrd`/`.cmdline` and a `manifest.json` (sha256 +
  size per artifact) — verified as a matched unit against the manifest at download (full
  hash) and at launch (presence/size). A stale or mismatched artifact now fails with a clear
  "out of sync" error instead of an opaque boot/attestation failure. `chutes.guest.image_set`
  is the single manifest generator/verifier, used by the build, `publish-image.sh`, and the
  launcher; the boot artifacts previously had no integrity link at all.

### Changed
- `base_image` is a published **image-set directory** (qcow2 + boot artifacts +
  `manifest.json`), not a bare qcow2 — the only supported format. `quick-launch --download` /
  `--download-debug` fetch the whole set into `/var/lib/chutes/base-images/<variant>/` and
  verify it; the build (ansible) emits the per-variant `manifest.json` for both debug and
  prod. Keeping an old build means moving its directory aside before re-downloading
  (downloads overwrite in place).
- Launch is decoupled from download: a missing image set fails with a clear remediation
  message rather than being auto-downloaded. Stage sets explicitly with `--download` (or, in
  a build, via ansible).
- Base-image integrity is carried entirely by the manifest instead of a hand-maintained
  `EXPECTED_BASE_SHA256` (removed) — no per-release hash bump, no per-launch re-hash of the
  multi-GB image, and per-variant shas for debug and prod (the old single constant could
  represent only one).

### Removed
- The bare-qcow2 launch path and `quick-launch --skip-checksum`. Every image — including
  benchmark and custom images — is consumed as a verified image set.
