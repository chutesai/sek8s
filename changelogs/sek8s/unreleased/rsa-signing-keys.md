### Changed

- `AdmissionConfig` cosign key documentation updated to reflect that the
  dynamically-fetched cosign keys are now RSA-verified (not PGP-verified)
  against the attested root key before being written to tmpfs. Key paths and
  behavior are unchanged.
