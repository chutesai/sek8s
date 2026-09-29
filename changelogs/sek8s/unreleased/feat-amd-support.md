### Added

- AMD SEV-SNP attestation support. The attestation service selects its evidence
  provider at runtime from the guest device node (`/dev/tdx_guest` vs
  `/dev/sev-guest`) rather than being built for one platform.
- `SnpQuoteProvider` reads the PSP-signed attestation report through configfs-TSM,
  falling back to the `/dev/sev-guest` `SNP_GET_REPORT` ioctl on kernels whose
  sev-guest driver registers no TSM provider. There is no external quote-generation
  daemon on SNP — the PSP answers guest requests directly — so no vsock socket is
  involved.
- `QuoteProvider` base class holding the report-data binding shared by both TEEs:
  `nonce || sha256(server cert public key)` in the 64-byte report-data field.

### Changed

- `AttestationResponse` carries the evidence in `quote` on every platform (a TDX quote or the
  raw SEV-SNP report), the field the API reads; it tells the platforms apart by the bytes.
  `tdx_quote` is gone: the API reads it only as a fallback for older VMs, so this release needs
  an API that reads `quote`.
- `TdxQuoteProvider` inherits the shared cert-hash and report-data plumbing; its
  quote generation is otherwise unchanged.
