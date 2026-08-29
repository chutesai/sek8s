### Added

- System status `/services` allowlist now covers the guest units that were previously invisible to
  tooling: `chute-log-shipper`, `signing-keys-config`, `registry-tls-config`,
  `verify-apparmor-profiles`, and `opa`. The prod VM has no console or SSH access, so an unlisted unit
  cannot be status-checked or log-tailed by the miner CLI at all.
