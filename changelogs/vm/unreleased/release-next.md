### Fixed

- The base image shipped the SSH public key of whichever host built it. `remove-ssh` purges
  `authorized_keys` in the guest build, but never runs in `base-image.yml`, so the key cloud-init
  seeded into the build VM stayed in the published layer. Deleting it is safe: `run-vm` writes fresh
  user-data and mints a new seed ISO on every boot, and the `cloud-init clean` immediately before
  means cloud-init re-provisions from that seed, so the consumer's own key is installed at boot and
  this copy was only a stale leftover. `base-image.yml` now purges `authorized_keys` for every
  account and asserts none survive, as `remove-ssh` does.

### Changed

- `base-image.yml`'s header no longer claims a third party can rebuild the layer and compare hashes.
  Rebuilding reproduces the layer's **contents**, not the qcow2 bytes: the format allocates clusters
  in write order, so two builds of the same packages differ in the container while agreeing on the
  files. Measured across an Intel and an AMD host — 201,694 filesystem entries, 28 differing, all
  build-time ephemera (logs, a journal directory named after a random machine-id, snapd state,
  apt/ESM and swcatalog caches, and the two initrds), identical package set at identical versions,
  and nothing differing under a measured path. `base_image_sha256` proves a consumer received our
  artifact; the test of a correct rebuild is the guest measurements, not the layer hash.
  `docs/reproducing-measurements.md` says the same, so nobody reports a hash mismatch as a bug.
  Also documents the one input that is still unpinned: `/var/lib/ubuntu-advantage/apt-esm/` comes
  live from `esm.ubuntu.com` and did differ between those builds — harmless only because it falls
  outside the measured paths.
