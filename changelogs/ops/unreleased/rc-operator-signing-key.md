### Added
- **RC-gate operator signing key injection.** `config.yaml` gains an optional `rc.operator_signing_key`
  (host path to the operator RSA private key), also settable via `quick-launch.sh --operator-signing-key`.
  The referenced key is copied onto the per-VM config volume as `operator-signing-key.pem` (mode 0600),
  where the RTMR2-measured initramfs (`rc-sign`) signs the attestation nonce with it for `rc=true`
  measurements — the API verifies with the matching public key. The key is referenced by path (never
  inlined in the config), and never leaves the config volume + initramfs `/run` tmpfs. Completes the
  producer side of the RC-gate flow (the initramfs consumer already existed).
