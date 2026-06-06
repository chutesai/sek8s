### Changed

- Pin host kernel to `linux-image-6.17.0-35-generic` in both Ubuntu 25.10 and
  26.04 host profiles to guarantee RTMR0 measurement consistency across the
  fleet. Previously the `linux-image-generic` metapackage was used, which
  allowed hosts to silently diverge after routine apt upgrades, causing
  attestation failures.
- Host setup now enforces that `kernel_package` is a pinned versioned package
  and rejects metapackages (e.g. `linux-image-generic`) at startup.
- `_get_kernel_version()` in host setup now handles pinned kernel package names
  directly instead of requiring `apt show` resolution via Depends.
