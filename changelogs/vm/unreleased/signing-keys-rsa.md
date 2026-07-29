### Changed

- Signing-keys bundle verification switched from detached OpenPGP (`gpgv`) to
  raw RSA (PKCS#1 v1.5, SHA-256). The `fetch-signing-keys` initramfs script and
  the build-time Helm-key verification in the `chutes-gpu` role now verify each
  key's signature over its raw (base64-decoded) bytes with
  `openssl dgst -sha256 -verify`. The trust chain (RTMR1 + RTMR3 attest the
  baked-in root key → root key verifies the signature → signature authenticates
  the leaf key), the JSON bundle shape, the `/run/chutes/signing-keys/` output
  paths, and the fail-closed behavior are unchanged. `helm-pubkey.gpg` stays a
  byte-identical OpenPGP key file for Helm; only the signature over it changed.
- The root trust anchor is now the RSA public key
  `/etc/chutes/root-signing-key.pem`, replacing `root-signing-key.gpg`. It is
  baked into the same measured locations (initramfs → RTMR1, `/etc/chutes/` →
  RTMR3), so tampering still changes the measurement.

### Removed

- The `signing-keys` role no longer installs or stages `gpgv`; the RSA verifier
  (`openssl`) is already staged for the LUKS/TLS paths and is reused.
