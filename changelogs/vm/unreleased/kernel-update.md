### Changed

- Bump VM version to 1.3.1 for new RTMR0 measurements. The guest image is
  unchanged; RTMR0 changes because QEMU now pins SMBIOS type 1/2/3 identity to
  static values, removing per-server motherboard drift from RTMR0 within a
  profile. Topology-driven variance (type 4/17) is still absorbed per-profile.
