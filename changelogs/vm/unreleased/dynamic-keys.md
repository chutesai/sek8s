### Changed

- Cosign public keys (`chutes.pub`, `dockerhub.pub`) and the Helm PGP keyring (`helm-pubkey.gpg`) are no longer baked into the VM image. They are now fetched dynamically at boot from `VALIDATOR_BASE_URL/servers/signing-keys`, verified against an attested root PGP key, and written to `/run/chutes/signing-keys/` (ephemeral tmpfs). Key rotation no longer requires an image rebuild or RTMR3 change.
- New `signing-keys` Ansible role installs the root PGP public key to `/etc/chutes/root-signing-key.gpg` (measured in RTMR3), deploys `signing-keys.conf` with the API URL, and installs the `fetch-signing-keys` initramfs init-bottom script and its hook.
- `fetch-signing-keys` initramfs script verifies each key's detached PGP signature with `gpgv` against the attested root key before writing to tmpfs. Any signature failure powers off the VM (fail-closed).
- `admission-controller.env` and `cosign-registries.json` updated to reference `/run/chutes/signing-keys/cosign/` paths.
- Helm chart provenance verification (`04-helm-chart-upgrade.sh`) updated to read keyring from `/run/chutes/signing-keys/helm-pubkey.gpg`.
- Build-time Helm keyring is now fetched from the signing-keys API and PGP-verified on the build host (same trust chain as boot-time fetch). The key is written to `/tmp/` for the `helm upgrade --install` call and deleted immediately after. No leaf key files (`helm-pubkey.gpg`, `chutes.pub`, `dockerhub.pub`) need to be distributed to build machines — only the root PGP public key is required.
- `/etc/admission-controller/cosign` removed from RTMR3 measurement path list (`tdx-measure-miner.conf`). Trust in cosign keys is now delegated to the PGP chain rooted at the measured `/etc/chutes/root-signing-key.gpg`.
- AppArmor profile `sek8s.system-manager` updated to allow reads from `/run/chutes/signing-keys/`.

### Removed

- `cosign_chutes_public_key_path`, `cosign_dockerhub_public_key_path`, and `helm_chart_public_key_path` inventory variables removed. Build machines now only require the root PGP public key (`root_signing_key_path`).

### Added

- `ansible/guest/roles/signing-keys/` — new role for root-of-trust PGP key installation and initramfs key-fetch machinery.
