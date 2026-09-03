### Fixed

- Guest image builds are reproducible across rebuilds of identical source again. Five values
  changed on every build and all reached RTMR2, so no two builds produced the same measurements —
  defeating the third-party verification `inventory-reproduce.yml` documents. Continues the same
  effort as the earlier admission-controller TLS and pinned-kernel fixes.
  - **LUKS container UUID** — random per `luksFormat`, and written into `cryptroot/crypttab`
    inside the initramfs. Now derived from version + build type via `to_uuid`.
  - **ext4 root filesystem UUID** — random per `mkfs.ext4`, and written into the kernel cmdline as
    `root=UUID=` by `stage-boot-artifacts`. Pinned the same way.
  - **`k3s-install.sh`** — fetched unpinned from `get.k3s.io` into `/usr/local/bin`, which is
    measured wholesale, and never used again after install. Removed once k3s is installed, so
    image measurements no longer depend on what upstream happened to serve at build time. The k3s
    binary itself stays version-pinned via `INSTALL_K3S_VERSION`.
  - **`overlayroot` and `mdadm` initramfs hooks** — both unused cloud-image features that wrote
    per-build data into the initramfs: `overlayroot` a fresh `/.random-seed`, `mdadm` a generation
    timestamp in `mdadm.conf`. Their hooks are now removed before the final `update-initramfs`.
    RAID is a host concern here (`ansible/host/playbooks/storage-setup.yml` builds `md0`); the
    guest is handed individual virtio-blk devices. Removing the hooks rather than the packages
    avoids dependency risk and is durable, since no apt operation follows in the build.

  Neither pinned UUID is secret: the LUKS key is rotated on first boot and the base image is copied
  per VM. Deriving both from version and build type keeps them stable across rebuilds, distinct per
  version, and never shared between a debug and a production image.

### Security

- The `overlayroot` initramfs hook is no longer shipped in the guest. Besides its per-build random
  seed, it installed an `init-bottom` script capable of mounting an overlay over the root
  filesystem, and it sorted ahead of `rtmr3-measure` — an unused root-remount mechanism in the
  measured boot path, ahead of the step that measures the root. Triggering it required either the
  `overlayroot=` kernel cmdline (measured into RTMR2, so detectable) or an edit to
  `/etc/overlayroot.conf` (behind the LUKS key, so post-attestation), but it had no reason to be
  in the boot chain.
