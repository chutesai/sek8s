### Added

- `sek8s_common.hotkey.load_miner_keypair(ss58, private_key, seed)`: the miner keypair from the
  64-byte sr25519 private key when set, else the seed, checked against `MINER_SS58`. Shared by
  system-manager and the attestation proxy; errors never contain key material.
- system-manager's `MinerConfig` reads `MINER_PRIVATE_KEY` and signs validator requests with it
  when set, else with `MINER_SEED`.

### Changed

- system-manager refuses to sign with a key whose address is not `MINER_SS58`, instead of signing
  as another hotkey, and `MinerConfig.miner_keypair` returns None when no key is set (it called
  `create_from_seed(None)` and raised).
