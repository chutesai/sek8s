### Added
- RC gate for debug/RC VMs: the debug image boots a fail-open initramfs that provisions
  against the production network (validator auth, VM root CA registration, k3s encryption)
  by proving possession of an authorized operator key — a detached RSA signature over the
  boot nonce sent in `X-Operator-Signature`. Only an authorized operator can bring a debug
  VM up against prod, and it can never join real traffic; the debug initramfs carries a
  distinct measurement (registered `rc: true`).
- Offline per-topology RTMR0 generation (`guest-tools/measurement/generate_measurements.py`,
  wired via the new `compute-rtmr0` role): reconstructs RTMR0 for every supported GPU
  topology by splicing per-topology events from the `tdx-measure` fork into a captured
  baseline CCEL — no per-topology hardware boot. `measurement_profile` selects one profile
  or, when empty, all profiles.
- Debug images now compute full RTMR1/2/3 (registered `rc: true`) so they attest under the
  RC gate.

### Changed
- The `luks` role now runs for **all** guest images and gates internally on the build type:
  prod encrypts the root filesystem and installs the fail-closed initramfs; debug installs
  the fail-open RC initramfs and performs no encryption. Prod and debug carry distinct
  initramfs measurements.
- Boot and storage-provisioning logic refactored into shared initramfs libraries
  (`attest-common`, `provision-common`) sourced by both prod and debug entry scripts, so the
  two stay in sync without leaking debug code into prod.
- Measurement pipeline restructured into explicit phases with one peer role per register —
  gather (`stage-boot-artifacts` + `capture-ccel`) then compute (`compute-rtmr1-2` +
  `compute-rtmr0`); RTMR1/2 now computed post-luks (after the initrd is final). Measurement
  controls collapsed to a single `measurements: none | offline | full` flag.
- CVM mTLS operations now use the `cvm.chutes.ai` domain.
- HWE kernel bumped to `7.0.0-28.28~24.04.1`.

### Removed
- Retired the userspace debug k3s secrets-encryption path (build-time static key baked at
  `/etc/chutes` + k3s systemd drop-in). Debug now writes the k3s EncryptionConfiguration from
  initramfs like prod (from a static well-known key), so debug and prod share the boot flow.
