### Added
- **`host-tools/scripts/generate-operator-signing-key.sh`** — standalone helper to mint an
  RC-gate operator RSA key pair for testing. Writes the PRIVATE key (referenced from
  `config.yaml` as `rc.operator_signing_key`) and the matching PUBLIC key (to register with the
  Chutes API's accepted RC measurement). Thin `openssl` wrapper (`genpkey` + `pkey -pubout`) whose
  keys are compatible with the initramfs `rc-sign` signer and the API verifier
  (`openssl dgst -sha256 -sign`/`-verify`); it writes no config and registers nothing.
