### Added

- The initramfs hotkey signer accepts the hotkey's 64-byte sr25519 private key
  (`miner-private-key` on the config volume, `sr25519 --private-key-file`), the credential in
  current Bittensor hotkey files, some of which have no `secretSeed`. The config volume carries
  exactly one of the two; a volume with both fails before boot attestation.
- `process-config.py` accepts `miner-private-key` (128 hex characters) in place of `miner-seed`,
  copies it to the k3s credentials dir and exports it as `MINER_PRIVATE_KEY` in system-manager's
  `miner.env`. Only the key the config volume carries is kept: switching keys removes the other's
  copy from the storage-backed credentials dir. Seed-only volumes produce the same files as
  before.
- The `miner-credentials` secret in `chutes` and `attestation-system` carries `ss58` plus the
  config volume's one key, `privateKey` or `seed`. `03-k3s-miner-credentials.sh` removes the other
  key, including the build-time `seed` placeholder the chart creates, so the secret never holds
  both. Seed-only VMs keep a byte-identical secret.

### Changed

- The initramfs signer rejects a `0x`-prefixed seed. `process-config.py` already rejected one,
  so such a VM used to pass boot attestation and the LUKS rotation and then fail in userspace;
  it now fails before attestation.
- `process-config.py` requires `miner-ss58` and exactly one key on the config volume. A volume
  with no miner credentials, a partial set or both keys now fails at config processing; before,
  missing credentials were skipped as "benchmark mode" (benchmark launches boot the separate
  benchmark image) and the VM powered off later in k3s setup.
