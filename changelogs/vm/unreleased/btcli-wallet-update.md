### Added

- The initramfs hotkey signer accepts the hotkey's 64-byte sr25519 private key
  (`miner-private-key` on the config volume, `sr25519 --private-key-file`), the credential in
  current Bittensor hotkey files, some of which have no `secretSeed`. The config volume carries
  exactly one of the two; a volume with both fails before boot attestation.

### Changed

- The initramfs signer rejects a `0x`-prefixed seed. `process-config.py` already rejected one,
  so such a VM used to pass boot attestation and the LUKS rotation and then fail in userspace;
  it now fails before attestation.
