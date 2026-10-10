### Added

- The release-candidate hotkey proof-of-possession can be signed with the miner's sr25519 private
  key (`MINER_PRIVATE_KEY`, for hotkeys without a seed) as well as the seed (`MINER_SEED`). The
  private key is used when both are set.

### Changed

- A miner key whose address is not `MINER_SS58` disables hotkey signing, logged as an error,
  instead of signing as another hotkey. The proxy keeps serving either way.
