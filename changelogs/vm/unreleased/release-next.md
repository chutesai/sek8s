### Changed

- **The `luks` guest-build role is now `prepare-boot-image`.** It always did more than LUKS —
  encryption/debug-init, mount-config rewrite, boot + attestation initramfs scripts, the RTMR3
  canonical manifest, and the final measured initramfs — and the RTMR3 manifest generation moved
  into it (it can't be separated from building the post-encryption initramfs). Operators selecting
  this stage by tag now use `--tags prepare-boot-image` instead of `--tags luks`.

### Security

- **RTMR3 canonical hashes now cover every measured file, including privileged ones.** The
  boot-time integrity gate (`/etc/tdx-rtmr3-expected-hashes`) is now generated over the fully
  finalized image root — while assembling the boot image, right before the final initramfs is built
  — instead
  of mid-build. Previously it was computed while later build stages could still change files, which
  forced excluding files that aren't final yet (notably `/root/.ssh`, which gates privileged
  access) from pre-verification. Those files were measured into RTMR3 but not checked against a
  build-time constant at boot, so offline tampering wasn't caught by the local power-off gate. The
  gate now hashes each measured file in its true on-disk state, so nothing is excluded and a
  tampered `/root/.ssh`, `/etc/fstab`, or verification tool aborts boot before RTMR3 is extended.
