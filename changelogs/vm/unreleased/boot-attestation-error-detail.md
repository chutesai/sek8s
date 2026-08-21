### Changed
- **Boot attestation failures now surface the API's reason.** The initramfs LUKS client
  (`attest-common`, `setup_storage`) previously logged only a generic string and the HTTP
  status (e.g. `Authentication failed (HTTP 403)`) when the nonce fetch, attestation POST, or
  rotation confirm failed. It now reads the response body for a `detail` / `message` / `error`
  field (FastAPI's `detail` first) and appends it — single-lined and capped at 300 chars so a
  body can't mangle the console — so a miner sees the actual cause. The 401/403 case on the
  attestation POST is relabeled `Attestation rejected` (it is a measurement verdict, not an
  auth failure). Falls back to the prior generic string when the body carries no message.
