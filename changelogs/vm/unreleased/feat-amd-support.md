### Added

- The guest image boots on both Intel TDX and AMD SEV-SNP hosts. `load-tee` replaces
  `load-tdx` in the initramfs: it tries `tdx_guest`, then `sev-guest`, modprobes
  `tsm_report` and mounts configfs, so one image serves both platforms.
- udev rule granting the attestation service group access to `/dev/sev-guest`,
  mirroring the existing `tdx_guest` rule. The device is root-only by default and the
  service does not run as root, so without this the SNP evidence path fails with
  EACCES at report generation.

### Changed

- The initramfs attestation hook detects the TEE and posts its evidence under
  `tdx_quote` or `snp_quote` accordingly. The TEE check moved from function entry to
  after network setup, so a guest that cannot reach the API fails with a network
  error rather than a misleading TEE error.
- `rtmr3-measure` exits successfully on SEV-SNP. RTMR3 is an Intel runtime
  measurement register and SNP has no equivalent — its single launch digest is fixed
  when the VM starts. TDX keeps its fail-closed behaviour, and a guest with *neither*
  device still fails closed, so a TDX guest whose module failed to load cannot be
  mistaken for SNP and let through unmeasured.

### Fixed

- `rtmr3-verify` now gates on the TEE exactly as `rtmr3-measure` does: verify on TDX,
  skip on SEV-SNP, fail closed with neither device. Only the initramfs half had the
  gate, so an SNP guest reached the TDX quote path, found no RTMR3, and failed (a
  production build would power itself off; a debug build crashed on `None.hex()`).
  Either way the unit failed, and k3s — which `Requires=` it — never started.

### Notes

- The skip above is a real reduction in what a SEV-SNP guest attests: the runtime file
  measurements have no SNP counterpart. Closing that gap needs dm-verity (putting
  root-filesystem integrity in the launch digest) or a vTPM at VMPL0 — both deliberate
  design decisions rather than something to paper over at boot.
- Ubuntu 24.04's GA 6.8 kernel ships no `sev-guest` module, so an SNP guest must run
  the HWE kernel, which provides both configfs-TSM and `/dev/sev-guest`.
