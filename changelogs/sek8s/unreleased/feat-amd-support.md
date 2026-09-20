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

- `AttestationResponse` now carries both `tdx_quote` and `snp_quote`, exactly one
  of which is populated, rather than a single platform-neutral field. The evidence
  type is named by the field it arrives in, so a verifier can dispatch on the
  request body without a separate discriminator. Existing TDX verifiers read
  `tdx_quote` and are unaffected.
- `TdxQuoteProvider` inherits the shared cert-hash and report-data plumbing; its
  quote generation is otherwise unchanged.
