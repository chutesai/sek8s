### Added

- `firmware/OVMF.amdsev.fd` is pinned in the repo alongside `OVMF.inteltdx.fd`, with
  `firmware/PROVENANCE.md` recording its origin and digest, and `build-firmware.sh
  --amd-sev` to rebuild it from edk2. The SEV-SNP launch measurement is a hash of
  these exact bytes, so resolving firmware from `/usr/share/ovmf` would let a distro
  package update silently invalidate every published measurement.

### Changed

- `paths.firmware_path()` takes an optional firmware filename instead of hardcoding the
  TDVF. It defaults to `GUEST_FIRMWARE`, so every existing caller is unchanged; the
  SEV-SNP path passes the AMD build. Still not overridable by user config — the TDX MRTD
  and the SEV-SNP launch digest are both computed over these exact bytes.

### Fixed

- `build-firmware.sh` reached `edksetup.sh` with the caller's positional parameters still
  set. A sourced script inherits `"$@"`, so any flag passed to `build-firmware.sh` arrived
  at edksetup as an unknown option, whereupon it printed usage and returned *without*
  configuring the build environment. `--secure-boot` had been broken this way all along;
  only the no-argument default ever worked.
