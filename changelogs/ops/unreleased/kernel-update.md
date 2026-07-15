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
- Pin SMBIOS type 1/2/3 (system/baseboard/chassis identity) to static values in
  the QEMU launch. TDVF folds the fw_cfg `etc/smbios/smbios-tables` blob into
  RTMR0, so motherboard-identity fields previously made two servers of the same
  profile produce different RTMR0 values; pinning them removes that per-server
  drift. This does not make RTMR0 host-independent — type 4/17 (processor/memory)
  tables still vary with `-smp`/`-m`/topology, absorbed by the per-profile
  measurement baseline, and type 0 (BIOS) is not overridden.
- Apply the same SMBIOS pinning in `extract-acpi.sh` so the extracted golden
  RTMR0 matches what is launched.
