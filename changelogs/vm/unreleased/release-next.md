### Fixed

- Fixed `00-reencrypt-secrets.sh` powering off the VM on fresh storage volumes. The
  at-rest verifier now ignores kine tombstone rows (`deleted != 0`) so that secrets
  or configmaps created and deleted at build time — which the online re-encrypt loop
  cannot reach because the apiserver no longer lists them — are not misread as live
  plaintext. The purge step now also scrubs any plaintext left in those tombstone
  values, preserving the no-plaintext-at-rest guarantee.

### Changed

- `00-reencrypt-secrets.sh` paths (`STATE_DB`, `ENCRYPTION_CONFIG`, `K3S_CONFIG`,
  `LOG_FILE`) are now environment-overridable (production defaults unchanged) so the
  script can be exercised against an isolated k3s in integration tests. Corrected the
  stale header comment that claimed the build-time `state.db` is deleted on fresh boot.

### Added

- `tests/integration/test_reencrypt_secrets_k3s.py`: opt-in (`SEK8S_K3S_IT=1`) end-to-end
  test that drives the real script against a throwaway k3s, reproducing the fresh-volume
  flow (unencrypted boot with a deleted-secret tombstone, then encrypted boot) and
  asserting it finalizes. Added kine `deleted`-column tombstone regression cases to
  `tests/shell/test_reencrypt_verifier.py`.
