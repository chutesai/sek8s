### Changed
- `fetch_key_and_unlock` (initramfs init-premount): the boot nonce endpoint (`/servers/nonce`) is
  now fetched via the mTLS proxy (`TDX_BASE_URL`) instead of the regular TLS API
  (`VALIDATOR_BASE_URL`), matching the API-side change that validates the miner cert during nonce
  issuance.
- `fetch_key_and_unlock`: the nonce request now includes the miner hotkey as the `miner_hotkey`
  query parameter (`?miner_hotkey=<hotkey>`), binding the nonce to the requesting miner. The API
  enforces that the same hotkey appears in the subsequent boot attestation POST body; nonces issued
  without a hotkey are rejected by the server as legacy.
- All initramfs API calls now go exclusively through the mTLS proxy (`TDX_BASE_URL`). The LUKS
  root-rotation confirm (`fetch_key_and_unlock`) and storage/cache rotation confirm
  (`setup_storage`) previously used the regular TLS API; both now use `TDX_BASE_URL` with the
  ephemeral client certificate. In `setup_storage` the mTLS cert deletion is deferred from
  `post_sync_keys` to `confirm_rotation` so the cert is available for the confirm call; it is also
  cleaned up in `clear_sensitive_data` as a safety net for boots where confirm is skipped.
- `VALIDATOR_BASE_URL` is no longer required or validated by initramfs scripts. It remains in
  `tdx-luks.conf` for post-boot services (system-manager).
