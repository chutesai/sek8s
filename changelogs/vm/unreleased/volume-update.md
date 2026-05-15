### Added

- **Ephemeral k3s admin credentials**: The k3s cluster admin kubeconfig is now
  purged at two points in every boot cycle — once during initramfs before any
  userspace runs, and once after cluster initialization completes — so it exists
  only while the cluster is actively serving requests.  Each purge is followed
  by an RTMR3 measurement: if the file is absent (expected), RTMR3 is unchanged;
  if it unexpectedly persists, its hash is extended into RTMR3 and attestation
  will reject the boot.  k3s regenerates the kubeconfig at startup so cluster
  operation is unaffected.

- **LUKS passphrase rotation**: Storage and cache volume passphrases are now
  rotated on every boot.  Rotation uses a two-phase key-slot approach
  (`luksAddKey` then `luksRemoveKey` after API confirmation) so the volume always
  has at least one valid key regardless of crash timing.  A fallback key is
  returned when a previous rotation was interrupted, ensuring clean recovery
  without operator intervention.  Legacy API responses continue to work
  unchanged.

- **k3s cluster secrets encryption**: Kubernetes Secret and ConfigMap values are
  now encrypted at rest.  The encryption key is fetched from the API at boot,
  wrapped with the same boot-token protection as LUKS passphrases, and written
  exclusively to tmpfs (`/run`).  The key is never written to persistent storage.
  A new key is generated when a storage volume is initialised for the first time;
  on all subsequent boots the same key is returned so existing data remains
  readable across reboots and image upgrades.  If the API does not yet supply a
  key, an identity-only configuration is written so k3s starts cleanly without
  encryption (no regression from current behaviour).

- **Expanded RTMR3 coverage**: The TDX RTMR3 measurement chain now covers
  additional deterministic components of the runtime stack.  The initramfs
  pass (pre-pivot_root) measures the sek8s application source, OPA admission
  policies, and k3s cluster-init scripts in addition to the existing system
  files; all newly added paths are also canonical-verified against build-time
  hashes so any offline modification powers off the VM rather than allowing
  a compromised image to boot.  A new `rtmr3-runtime-measure` systemd service
  extends this chain after bind mounts are established and before k3s starts,
  measuring the k3s static manifests from their storage-volume location; this
  confirms that the content k3s actually reads matches what was synced from the
  verified image.

- **`fetch_key` initramfs hook**: Added `sha384sum` to the set of binaries
  included in the initramfs image.

### Changed

- **k3s server config**: Added `encryption-provider-config` pointing to the
  ephemeral key path described above.

- **k3s cluster-init**: `k3s-cluster-init.service` keeps `Requires=k3s.service`
  as the correct dependency. The secrets re-encryption script no longer stops and
  restarts k3s to perform the kine purge — DELETE and UPDATE operations now run
  online (SQLite WAL mode allows concurrent access), removing a systemd dependency
  cascade that was previously killing the service mid-run.

- **k3s secrets re-encryption marker**: The completion marker is now written only
  after both the kubectl re-encryption pass and the kine history purge succeed. A
  failed purge previously left plaintext dead rows and `old_value` data permanently;
  it now causes a full retry on the next boot.

### Fixed

- **LUKS attestation mTLS cert binding**: The ephemeral mTLS client certificate
  generated during `fetch_key_and_unlock` (init-premount) is now preserved across
  init stages so init-bottom (`setup_storage`) uses the same certificate for the
  `/luks/attest` call. The TDX quote REPORTDATA for `/luks/attest` now includes
  `nonce + cert_hash`, binding the quote cryptographically to the certificate
  presented in the mTLS handshake and matching the boot attestation pattern the
  API expects. Previously only the nonce was included, causing a 403 from the API.
