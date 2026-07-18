### Added

- `vm-tls` role with `setup_vm_tls`, an initramfs `init-bottom` script
  (`PREREQ=setup_storage`) that owns the full VM mTLS cert lifecycle — per-boot
  4096-bit VM root CA generation, validator registration
  (`PUT /servers/{vm_name}/vm-root-ca`, TDX-attested, mTLS using the CA cert
  itself as the client credential), attestation-proxy server cert, registry mTLS
  client cert, and `ca.key` deletion — all within the RTMR2-measured initramfs
  before `pivot_root`. `ca.key` never exists in userspace.
- Attestation proxy server cert (`/run/chutes/proxy-tls/server.{key,crt}`) and
  registry mTLS client cert (`/run/chutes/registry-tls/client.{key,crt}`)
  generated on tmpfs each boot; containerd reads the client cert for direct mTLS
  pulls from `registry.chutes.ai`, and cosign reads it via
  `/etc/docker/certs.d/registry.chutes.ai/` symlinks.
- `sek8s.attestation-proxy` AppArmor profile confining the proxy container to
  its required paths; added to the apparmor-hardening install/verify wiring and
  to the RTMR3 measurement chain (`tdx-measure-miner.conf`).

### Changed

- Private registry pull auth moves from miner-hotkey-scoped (nginx proxy
  DaemonSet on NodePort 30500 at `localregistry.chutes.ai:30500`) to per-VM mTLS
  against `registry.chutes.ai`. Only an attested VM presenting a CA-signed client
  cert can pull. Backward compatibility is DUAL-AUTH and lives server-side (the
  validator/registry): old VMs keep the legacy miner-proxy path, new VMs present
  a client cert. The guest image carries no dual-path code.
- `registries.yaml.j2`: replaced the `localregistry.chutes.ai:30500` local-proxy
  mirror with a `configs: "registry.chutes.ai"` mTLS block pointing at the
  initramfs-written tmpfs client cert/key (no insecure-registry, no NodePort).
- `proxy-manifests.yaml.j2`: `host-certs` hostPath moved from
  `/etc/attestation-service/certs` to `/run/chutes/proxy-tls`; added the
  attestation-proxy AppArmor annotation.
- `cosign-registries.json.j2`, `opa-config-data.json.j2`, admission
  `allowed_registries`, and system-manager `IMAGE_PULL_ALLOWED_REGISTRIES`
  updated from `localregistry.chutes.ai:30500` to `registry.chutes.ai`. Removed
  the `allow_http` / `allow_insecure` cosign flags now that pulls use real TLS.
- `configure-cosign.yml`: removed the `127.0.0.1 localregistry.chutes.ai`
  `/etc/hosts` alias and the `insecure-registries` Docker daemon config that
  supported the old local proxy.

### Removed

- `setup-tls-certs.sh` userspace proxy-cert generator and its wiring in
  `attestation-service-init.service` / `install-attestation-init-service.yml`.
  The proxy server cert is now minted in the initramfs by `setup_vm_tls`.

### Notes

- This change alters the RTMR3 measurement baseline (new AppArmor profile, edited
  service configs) and adds an RTMR2-measured initramfs script; measurement
  re-baselining is handled at release time.
- The chutes-miner chart registry DaemonSet/Service is intentionally NOT removed
  in this change — that retirement is a later, separate step gated on full fleet
  migration.
