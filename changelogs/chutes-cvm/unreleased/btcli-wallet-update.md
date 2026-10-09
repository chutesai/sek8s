### Added

- `miner.private_key` (`--miner-private-key`): the miner hotkey's 64-byte sr25519 private key, a
  current Bittensor hotkey file's `privateKey`, accepted in place of `miner.seed` for hotkeys
  whose file has no `secretSeed`. It is written to the config volume as `miner-private-key`, and
  the host-side shutdown and Chutes API calls sign with it.

### Changed

- The launch config takes exactly one of `miner.private_key` or `miner.seed`, as plain hex
  without `0x` (128 and 64 characters). Both set, a `0x` prefix or a wrong length are refused when
  the config is loaded, before anything launches; the guest refused these at boot.
- Host-side signing refuses a key that does not belong to `miner.ss58`. The Chutes API calls used
  to warn and sign with the key's own hotkey, which the API then rejected.
