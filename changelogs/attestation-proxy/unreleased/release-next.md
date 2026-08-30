### Added

- The external proxy attaches a miner-hotkey proof-of-possession
  (`X-Chutes-Hotkey`/`X-Chutes-Nonce`/`X-Chutes-Signature`) to each response when a `MINER_SEED`
  is configured, so the validator can authorize release-candidate measurements at runtime. The
  seed is optional: proxies without it (older charts) pass through unsigned, so existing VMs keep
  working.
