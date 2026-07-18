### Changed

- The attestation proxy now runs its two ports (external 8443, internal 8444) via
  the shared `WebServer.serve()` instead of a bespoke `run_server_async()` that
  hand-rolled a `uvicorn.Config` and silently dropped `mtls_required` /
  `client_ca_path` / `require_tls`. Each port now honours its full TLS/mTLS/bind
  config with no per-call-site server wiring that could drift.
- The external port (8443) presents the initramfs-minted, CA-signed server cert;
  the validator pins it to the VM's registered CA and authenticates with signed
  request headers. Client-cert mTLS (`MTLS_REQUIRED`) is intentionally NOT
  enabled on the proxy — it is not how validators authenticate.

### Removed

- `run_server_async()` — replaced by the shared `WebServer.serve()`.
