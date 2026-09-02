### Fixed

- `--target-os` now rewrites every OS-derived field of the submitted host profile, not just
  the QEMU used for the readiness check. A host asking about (or registering for) a release it
  has not upgraded to yet no longer submits its live QEMU, `-cpu` args and OS release — e.g. a
  25.10 host targeting 26.04 previously registered "26.04 + QEMU 10.1.0", a pair 26.04 never
  ships. `host verify --target-os ... --submit` now registers the target class too.
- `host submit-profile` on an unsupported OS release (or a supported one running a QEMU it does
  not ship) is now rejected locally, as `host verify` already was. It previously registered the
  host class anyway, spending a measurement-generation slot on a class that could never attest.

### Added

- `chutes-cvm host submit-profile --target-os <release>` — register the host class this machine
  becomes after an OS upgrade (target release, its QEMU, its `-cpu` args), mirroring
  `host verify --target-os`. Unsupported releases are rejected before anything is signed or sent.
