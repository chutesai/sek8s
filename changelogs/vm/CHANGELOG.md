# Changelog

All notable changes to the VM / guest image will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

Version source of truth: `ansible/guest/VERSION`

## [1.3.1] - 2026-05-29

### Added
- New initramfs script `write-validator-auth` (init-bottom) writes the per-VM ephemeral validator auth SS58 to `/run/chutes/validator-auth.env` — directly in the initramfs `/run` tmpfs, which `initramfs-tools` moves to the real root's `/run` before exec'ing init. The file is fully ephemeral (cleared on every reboot, never touches the root filesystem), and the write logic is measured into RTMR2. VM powers off on invalid or missing SS58.
- New cluster-init script `03-k3s-validator-auth.sh`: creates or updates the `validator-auth` K8s Secret in the `attestation-system` namespace with the per-VM ephemeral SS58 on every boot (no run-once marker), then restarts the attestation-proxy DaemonSet to apply the new `ALLOWED_VALIDATORS` value. Added to `SECURITY_CRITICAL_SCRIPTS` so a failure causes VM poweroff.
- `system-manager.service` now loads `/run/chutes/validator-auth.env` as a second `EnvironmentFile`. Since this file is ephemeral and can never be present if `write-validator-auth` did not run, the service correctly fails to start if the initramfs script was skipped — safe failure by design.
- RTMR3 measurement hardening: added `/etc/system-manager/system-manager.env`, `/etc/admission-controller/admission-controller.env`, `/etc/admission-controller/cosign-registries.json`, `/etc/docker/daemon.json`, `/etc/rancher/k3s/registries.yaml`, `/etc/hosts`, and `/etc/tdx-luks.conf` to `tdx-measure-miner.conf`. These were previously unmeasured, allowing offline tamper of registry allowlists, cosign config, or attestation endpoints without detection.
- New Ansible role `apparmor-hardening`: installs AppArmor profiles, abstractions, systemd drop-ins, and a boot-time profile verification service (`lock-mac-caps.service`).
- AppArmor abstraction `sek8s-cache-deny`: denies shell/interpreter access to the HF model cache volume (`/var/snap/cache/`). Debug builds use `audit deny` for kernel audit logging; production builds use silent `deny`.
- AppArmor abstraction `sek8s-secrets-deny`: denies shell/interpreter access to boot secrets (`/run/chutes/`), containerd socket, k3s token, and miner credentials.
- AppArmor profile `sek8s.system-manager`: named profile applied via systemd `AppArmorProfile=` — grants cache rw, credential read, containerd socket, and network access.
- AppArmor profile `sek8s.setup-cache`: named profile for the setup-cache service — grants cache rw and coreutils, no network or credentials.
- AppArmor profile `sek8s.deny-sensitive-default`: auto-attaches to common shells, interpreters, and data-transfer tools (bash, dash, sh, cat, cp, tar, rsync, curl, wget, perl, etc.) — includes both deny abstractions to block access to protected paths.
- `verify-apparmor-profiles.service`: oneshot that verifies all sek8s AppArmor profiles are loaded in enforce mode at boot. Powers off the VM on failure.
- RTMR3 progress logging: per-directory collection progress and periodic hashing progress (every 200 files) logged to `/dev/kmsg` during the expanded measurement phase.
- `ansible/guest/roles/luks/files/initramfs/luks-helpers`: shared initramfs shell library with `write_key_file`, `shred_key_file`, `luks_add_key`, `luks_remove_key` — sourced by both `fetch_key_and_unlock` (init-premount) and `setup_storage` (init-bottom).
- Root LUKS passphrase rotation in `fetch_key_and_unlock` (init-premount): detects first-boot LUKS2 token (id 15, type `chutes-first-boot`), sends `first_boot` flag in boot attestation POST, enforces mandatory rotation on every boot — adds new key slot, confirms with API, then kills all pre-existing slots by number to ensure no stale keys remain on the device.
- `ansible/guest/roles/rtmr3-measure/files/tdx-measure-miner.conf` and `tdx-measure-gpu.conf`: extended RTMR3 measurement coverage to additional filesystem paths not previously included:
  - `/usr/lib/systemd/system`
  - `/etc/fstab`
  - `/var/spool/cron/crontabs`
  - `/etc/init.d`
  - `/etc/rc.local`
  - `/root/.bashrc`, `/root/.bash_profile`, `/root/.profile`
- `tdx-measure-gpu.conf` aligned to the same measurement tiers as `tdx-measure-miner.conf`: systemd unit dirs, ld.so config, modprobe, sysctl, profile, environment, fstab, crontabs, init scripts, root shell startup files, and the `/usr/local/bin`, `/usr/local/sbin`, `/usr/bin`, `/usr/sbin`, `/usr/local/lib` binary tiers.
- `ansible/guest/roles/signing-keys/` — new role for root-of-trust PGP key installation and initramfs key-fetch machinery.

### Changed
- Split cosign signature verification into two keys: `chutes.pub` for the private localregistry (and wildcard fallback), `dockerhub.pub` for Docker Hub `parachutes/*` images
- Renamed Ansible inventory vars: `cosign_public_key_path` -> `cosign_chutes_public_key_path` (`~/.cosign/chutes.pub`) and added `cosign_dockerhub_public_key_path` (`~/.cosign/dockerhub.pub`)
- Renamed admission controller env vars: `CHUTES_COSIGN_PUBLIC_KEY_PATH` -> `CHUTES_PUBLIC_KEY_PATH`, added `DOCKERHUB_PUBLIC_KEY_PATH`
- Generalised `_require_ctx_key` to validate against a set of trusted key paths (`required_key_paths`) rather than a single path
- `chutes_chart_version` bumped from `0.2.7` to `0.3.0` (`chutes-miner-gpu` Helm chart). The new chart replaces the per-validator nginx `map` routing with a single static `set $upstream_host` directive and a single `chutes-registry` NodePort Service, required for the static `localregistry.chutes.ai` hostname. See [chutes-miner#134](https://github.com/chutesai/chutes-miner/pull/134).
- Registry hostname decoupled from the validator hotkey: all `{{ validator | lower }}.localregistry.chutes.ai` references replaced with the static hostname `localregistry.chutes.ai` across k3s-prereqs.yml, registries.yaml.j2, configure-cosign.yml, opa-config-data.json.j2, cosign-registries.json.j2, admission-controller/defaults/main.yml, and system-manager.env.j2. Validator hotkey rotation no longer invalidates cosign signatures or requires a VM rebuild.
- `fetch_key_and_unlock` (initramfs, init-premount): now parses `vm_auth_ss58` from the boot attestation API response and saves it to `/run/chutes/validator-ss58` (mode 600). Boot fails with poweroff if the field is absent from the response.
- `proxy-manifests.yaml.j2`: removed the baked-in `validator-auth` Secret definition (it contained a hard-coded validator hotkey and is in the RTMR3-measured manifests directory). The Secret is now created at runtime by `03-k3s-validator-auth.sh`. The RBAC `secret-reader` Role updated to include `validator-auth` in `resourceNames`. The `wait-for-credentials` init container now also waits for the `validator-auth` Secret before the attestation-proxy pod starts.
- `system-manager.env.j2`: removed `ALLOWED_VALIDATORS` (now in unmeasured `validator-auth.env`). `IMAGE_PULL_ALLOWED_REGISTRIES` updated to use static `localregistry.chutes.ai` hostname. `system-manager.env` is now fully deterministic at build time and safe to include in RTMR3 measurement.
- `tdx-measure-miner.conf`: added three-tier RTMR3 measurement expansion — Tier 1 (custom binaries in `/usr/local/{bin,sbin}`), Tier 2 (code injection config paths), Tier 3 (system binaries in `/usr/bin`, `/usr/sbin`, and custom shared libs in `/usr/local/lib`). Also added service configs, AppArmor profiles, and systemd units not previously measured.
- `tdx-measure-miner.conf`: removed `/etc/rancher/k3s/registries.yaml` (runtime-modified by `process-config.py`, persists across reboots; security properties independently measured through other files).
- `pods.rego`: added `MAC_ADMIN` and `MAC_OVERRIDE` to `dangerous_capabilities` to prevent containers from modifying AppArmor profiles.
- `chutes-miner-vm.yml`: inserted `apparmor-hardening` role after `cache-volume` and before dynamic config services.
- `ansible/guest/roles/luks/tasks/luks_encrypt.yml`: added `type: luks2` to the LUKS container creation task (previously relied on cryptsetup default); added first-boot LUKS2 token task (`chutes-first-boot`, id 15) after container creation; added task to copy shared `luks-helpers` script into the initramfs.
- `ansible/guest/roles/luks/files/initramfs/fetch_key_and_unlock`: updated boot attestation POST body to include `first_boot` flag; added slot enumeration and `luksKillSlot`-based cleanup after successful rotation confirm; rotation confirm failure now rolls back cleanly and powers off; any key slot cleanup failure powers off rather than proceeding with stale slots.
- `ansible/guest/roles/luks/files/initramfs/setup_storage`: extracted LUKS helpers to shared `luks-helpers` file; `finalize_rotation` now uses `luksKillSlot` by slot number (cleaning up stale slots from prior incomplete rotations); any slot cleanup or rollback failure powers off.
- Cosign public keys (`chutes.pub`, `dockerhub.pub`) and the Helm PGP keyring (`helm-pubkey.gpg`) are no longer baked into the VM image. They are now fetched dynamically at boot from `VALIDATOR_BASE_URL/servers/signing-keys`, verified against an attested root PGP key, and written to `/run/chutes/signing-keys/` (ephemeral tmpfs). Key rotation no longer requires an image rebuild or RTMR3 change.
- New `signing-keys` Ansible role installs the root PGP public key to `/etc/chutes/root-signing-key.gpg` (measured in RTMR3), deploys `signing-keys.conf` with the API URL, and installs the `fetch-signing-keys` initramfs init-bottom script and its hook.
- `fetch-signing-keys` initramfs script verifies each key's detached PGP signature with `gpgv` against the attested root key before writing to tmpfs. Any signature failure powers off the VM (fail-closed).
- `admission-controller.env` and `cosign-registries.json` updated to reference `/run/chutes/signing-keys/cosign/` paths.
- Helm chart provenance verification (`04-helm-chart-upgrade.sh`) updated to read keyring from `/run/chutes/signing-keys/helm-pubkey.gpg`.
- Build-time Helm keyring is now fetched from the signing-keys API and PGP-verified on the build host (same trust chain as boot-time fetch). The key is written to `/tmp/` for the `helm upgrade --install` call and deleted immediately after. No leaf key files (`helm-pubkey.gpg`, `chutes.pub`, `dockerhub.pub`) need to be distributed to build machines — only the root PGP public key is required.
- `/etc/admission-controller/cosign` removed from RTMR3 measurement path list (`tdx-measure-miner.conf`). Trust in cosign keys is now delegated to the PGP chain rooted at the measured `/etc/chutes/root-signing-key.gpg`.
- AppArmor profile `sek8s.system-manager` updated to allow reads from `/run/chutes/signing-keys/`.
- `fetch_key_and_unlock` (initramfs init-premount): the boot nonce endpoint (`/servers/nonce`) is
  now fetched via the mTLS proxy (`TDX_BASE_URL`) instead of the regular TLS API
  (`VALIDATOR_BASE_URL`), matching the API-side change that validates the miner cert during nonce
  issuance.
- `fetch_key_and_unlock`: the nonce request now includes the miner hotkey as the `miner_hotkey`
  query parameter (`?miner_hotkey=<hotkey>`), binding the nonce to the requesting miner. The API
  enforces that the same hotkey appears in the subsequent boot attestation POST body; nonces issued
  without a hotkey are rejected by the server as legacy.
- All boot-sensitive initramfs API calls now go through the mTLS proxy (`TDX_BASE_URL`). The LUKS
  root-rotation confirm (`fetch_key_and_unlock`) and storage/cache rotation confirm
  (`setup_storage`) previously used the regular TLS API; both now use `TDX_BASE_URL` with the
  ephemeral client certificate. In `setup_storage` the mTLS cert deletion is deferred from
  `post_sync_keys` to `confirm_rotation` so the cert is available for the confirm call; it is also
  cleaned up in `clear_sensitive_data` as a safety net for boots where confirm is skipped.
  Exception: `fetch-signing-keys` continues to use `VALIDATOR_BASE_URL` — the signing-keys
  endpoint is intentionally public and does not require mTLS.
- `VALIDATOR_BASE_URL` is no longer required or validated by `fetch_key_and_unlock`. It remains in
  `tdx-luks.conf` for `fetch-signing-keys` (signing keys bundle fetch) and post-boot services
  (system-manager).

### Removed
- Hard-coded validator SS58 (`5Dt7HZ7Zpw4DppPxFM7Ke3Cm7sDAWhsZXmM5ZAmE7dSVJbcQ`) removed from all Ansible role defaults (`common`, `admission-controller`, `attestation-service`, `system-manager`) and inventory files (`ansible/guest/inventory.yml`, `local/inventory.prod.yml`). The `validator` Ansible variable is no longer used anywhere in the guest image build.
- `cosign_chutes_public_key_path`, `cosign_dockerhub_public_key_path`, and `helm_chart_public_key_path` inventory variables removed. Build machines now only require the root PGP public key (`root_signing_key_path`).

## [1.3.0] - 2026-05-18

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
- `roles/rtmr3-measure`: new standalone role that extends TDX RTMR3 at boot
  with SHA-384 hashes of a configurable list of paths (defaults: SSH keys,
  passwd, shadow, sudoers).  An `initramfs-tools` hook bakes the measurement
  script and path config into the initramfs (covered by RTMR1) so neither can
  be tampered with without changing RTMR1.  Any offline modification to a
  measured path — e.g. SSH key injection into an unencrypted image — produces
  a different RTMR3 that verifiers can detect at session start.
- `tdx-rtmr-extend`: small C binary installed to `/usr/local/bin/` that
  extends a TDX RTMR via `/dev/tdx_guest` ioctl (V2 and V3 ABIs) with a
  sysfs fallback for kernels ≥ 6.16.  Does not require libtdx-attest at
  runtime.
- `verify-access-config`: Python script installed to `/usr/local/bin/`
  for use by the partner inside the VM.  Displays SSH keys (fingerprints +
  comments), sshd config, user accounts, password status, and sudo rules.
  Replays the SHA-384 extend chain to compute the expected RTMR3, reads the
  live value from a TDX quote, and reports PASS/FAIL.  The script itself is
  in the measurement list so any tampering changes RTMR3.
- `chutes-miner-vm.yml` and `tee-gpu-vm.yml`: added `rtmr3-measure` play after
  security hardening and SSH key injection so the final on-disk state (including
  partner SSH keys) is what gets measured at boot.
- `guest-tools/scripts/compute-rtmr3.sh`: compute the expected RTMR3 at build
  time by mounting the final qcow2 read-only with `guestmount` and simulating
  the exact SHA-384 extension chain from `rtmr3-measure`. Eliminates the need
  to boot twice just to capture RTMR3 — the Ansible build runs this automatically
  and writes `<image>.rtmr3` alongside the qcow2 before the LUKS step.
- `ansible/guest/playbooks/chutes-miner-vm.yml`, `tee-gpu-vm.yml`: add
  `compute-rtmr3` play that runs `compute-rtmr3.sh` automatically after
  `finalize-vm-image` and before `luks`/`prime-vm`, writing the expected RTMR3
  to `<final_img_path>.rtmr3`.
- `playbooks/tee-gpu-vm.yml`: new dedicated TEE GPU VM build playbook, fully
  independent of `chutes-miner-vm.yml`. Includes `run-vm`, `common`, `gpu`,
  `benchmark`, `harden-ssh`, `lock-accounts`, `disable-console`, `security`,
  `rtmr3-measure`, `setup-ssh-access`, `cleanup-build-vm`, `finalize-vm-image`,
  and `prime-vm`. No k3s, admission controller, system-manager, cache-volume, or
  LUKS plays.
- `benchmark` Ansible role: self-contained TEE GPU VM image setup. Owns the full
  config volume stack (simplified `process-config.py`, `config-manager.service`,
  `var-config.mount`, `config-volume-validator.service`, `netplan-apply.service`)
  in addition to TDX/GPU attestation tools, LUKS helper, and storage setup service.
- `attest` in-VM tool: `attest dump` prints TDX hardware measurements (MRTD, RTMRs,
  MRSEAM); `attest verify` adds NVIDIA NRAS GPU attestation (ES384-signed JWT) and
  optional Intel Tiber Trust Services TDX remote verification.
- `luks-setup` in-VM tool: `luks-setup setup` performs full end-to-end LUKS2
  encryption of the benchmark storage volume (wipe, encrypt, format XFS, mount at
  `/data`); `luks-setup open` unlocks it after a reboot. Both commands default to
  `/dev/chutes-storage` and `/data`, requiring no arguments in the common case.
  The volume is intentionally not persisted to crypttab/fstab — explicit unlock
  is required on every reboot.
- Benchmark VMs use a simplified `process-config.py` (no k3s, miner credential, or
  Docker Hub logic) so the config volume can be created without miner credentials.
- `benchmark-storage-setup.sh` + `setup-storage-bind-mounts.service` (benchmark
  role): at boot, identifies the storage block device, creates a stable
  `/dev/chutes-storage` symlink and `/data` mount point. Auto-mounts the device
  if it already has a filesystem; logs `luks-setup` instructions otherwise.
  Service name matches the production service so existing systemd ordering
  constraints are satisfied without changes to `config-manager.service`.
- `docs/tee-gpu-vm.md`: operator reference for building and launching the TEE GPU VM.
- `docs/tee-gpu-vm-guide.md`: user-facing walkthrough covering SSH access, GPU
  verification, attestation, storage encryption, and network transparency.

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
- Moved libvirt/VM lifecycle handlers from `roles/run-vm/handlers/main.yml` to the
  top-level `ansible/guest/handlers/main.yml` so they are available to all guest
  roles rather than scoped only to `run-vm`.
- `gpu/tasks/device-setup.yml`: Docker NVIDIA Container Runtime is now configured
  here (alongside containerd) so benchmark images have Docker GPU support without k3s.
- `final_img_path` no longer appends a `-benchmark` suffix; use `build_env: "benchmark"`
  in inventory to produce a dedicated `image/benchmark/<version>.qcow2` output path.
- `chutes-miner-vm.yml` is now production-only — all `benchmark_build` conditions and
  the benchmark tools play have been removed. Benchmark builds use `tee-gpu-vm.yml`.
- **Playbook rename:** `site.yml` → `chutes-miner-vm.yml`. `tee-gpu-vm.yml` is a new
  dedicated TEE GPU VM build playbook (see Added).
- **Role refactor — single responsibility:** `harden-access` and `cleanup` were split
  into focused single-purpose roles. New roles: `harden-ssh` (sshd key-only auth),
  `lock-accounts` (password locking), `disable-console` (getty/serial masking + grub
  cmdline), `remove-ssh` (SSH and sudo removal for production builds), `setup-ssh-access`
  (partner key injection for TEE GPU VM), `cleanup-build-vm` (common build cleanup:
  cloud-init, infiniband, fstrim), `cleanup-orchestration` (k3s/attestation/admission
  teardown for production builds), `finalize-vm-image` (shutdown, undefine, move image).
- **`rtmr3-measure` role enhancements:**
  - Added `/etc/default/grub` to `rtmr3_measure_paths` and `rtmr3_canonical_paths` —
    any change to the GRUB kernel cmdline (e.g. re-enabling console) now changes RTMR3
    and fails the boot-time hash check.
  - `verify-access-config`, `tdx-rtmr-extend`, and `/etc/tdx-rtmr3-expected-hashes`
    are now set `chattr +i` (immutable) after install — accidental modification by root
    is blocked at the filesystem level.
  - `verify-access-config` gains a **Console Access Configuration** section: displays
    GRUB cmdline masking, checks live systemd state for all getty/serial services, and
    returns exit 1 if any console service is `active`.
- **`disable-console` role now sets GRUB default:** `GRUB_DEFAULT=0` and
  `GRUB_SAVEDEFAULT=false` are written to `/etc/default/grub`; `grub-set-default 0`
  pre-populates `/boot/grub/grubenv`. Together these ensure GRUB always selects the
  latest kernel entry deterministically without requiring a prime boot at the GRUB level
  (TDVF EFI variable priming still requires one actual boot cycle via `prime-vm`).
- **`prime-vm` role simplified:** removed ephemeral config/cache/storage volumes and
  dummy credentials. Now launches with `--image` + `--network-type user` only, polls
  the serial console log for `Linux version` (kernel first line), then force-kills.
  Works identically for both `chutes-miner-vm.yml` and `tee-gpu-vm.yml`.

### Fixed
- **LUKS attestation mTLS cert binding**: The ephemeral mTLS client certificate
  generated during `fetch_key_and_unlock` (init-premount) is now preserved across
  init stages so init-bottom (`setup_storage`) uses the same certificate for the
  `/luks/attest` call. The TDX quote REPORTDATA for `/luks/attest` now includes
  `nonce + cert_hash`, binding the quote cryptographically to the certificate
  presented in the mTLS handshake and matching the boot attestation pattern the
  API expects. Previously only the nonce was included, causing a 403 from the API.
- `setup_storage`: add `--batch-mode` and `timeout 60` to all `cryptsetup luksOpen`
  and `luksFormat` calls to prevent indefinite hangs in init-bottom; fix
  `purge_admin_kubeconfig` mount point from `/tmp` (not guaranteed in initramfs) to
  `/run`, and surface the mount error message instead of suppressing it.
- `process-config.py`: `apply_docker_hub_and_registries` now returns early with
  success when the `admission` group is absent and no Docker Hub credentials are
  present, instead of hard-failing. This prevented `config-manager.service` from
  starting in benchmark VMs (which have no admission controller).
- `attest verify`: GPU attestation now passes `options={"ppcie_mode": False}` to
  `get_evidence()`, matching how `chutes_nvevidence.NvClient.gather_evidence()`
  works — fixes evidence collection on H200s in Protected PCIe mode.
- `attest verify`: TDX verification replaced `trustauthority-cli` with
  `dcap_qvl.get_collateral_and_verify()` — no API key or config file required,
  same library used in production validator. `dcap-qvl` is now installed in the
  nvevidence venv.
- `verify-access-config`: `masked-runtime` (services masked via kernel cmdline
  `systemd.mask=`) is now accepted as a valid masked state alongside `masked`.
- Removed stale `trustauthority-cli` install task from `benchmark` role — nothing
  in the codebase calls it; remote attestation is handled by `attest.py` + `dcap_qvl`.
- Console access regression: `disable-console` play was missing from `tee-gpu-vm.yml`
  after role refactor, allowing getty to run. Now included unconditionally for both
  build types.

### Removed
- `roles/harden-access` — split into `harden-ssh`, `lock-accounts`, `remove-ssh`,
  `disable-console`.
- `roles/cleanup` — split into `cleanup-build-vm` (common build cleanup) and
  `cleanup-orchestration` (k3s/attestation/admission teardown for production builds).

## [1.2.0] - 2026-05-05

### Added
- Boot-time Helm upgrade script (`04-helm-chart-upgrade.sh`) refactored into a generic multi-chart dispatcher; per-chart configs in `/etc/chutes/chart-configs/` and optional override scripts in `/etc/chutes/chart-upgrade-overrides/` support custom upgrade logic (e.g. GPU Operator CRD migration)
- GPU Operator boot-time upgrade override script handles CRD migration with `--disable-openapi-validation` and `operator.upgradeCRD=true` for persistent clusters upgrading across major chart versions

### Changed
- Updated sek8s to 0.3.0: HuggingFace cache improvements including download cancellation, stale revision purging, and isolated download subprocess.
- k3s upgraded from `v1.33.7+k3s1` to `v1.35.4+k3s1`
- CUDA toolkit upgraded from `13-0` to `13-2`
- NVIDIA driver package upgraded from `595.58.03-1ubuntu1` to `595.71.05-1ubuntu1`
- GPU Operator Helm chart upgraded from `v24.9.2` to `v26.3.1`; build-time install now uses `operator.upgradeCRD=true`
- Helm CLI upgraded from `v3.11.3` to `v3.20.2`
- OPA upgraded from `0.68.0` to `1.15.2` (0.x to 1.x major bump; existing policy tests confirmed passing)
- cosign pinned to `v2.6.3` (previously fetched `latest` at build time, non-deterministic; fixes CVE-2026-39395)
- `nv-attestation-sdk` constraint bumped from `^2.6.2` to `^2.7.0` in `nvevidence/`

### Fixed
- OPA validating policy (`chutes.rego`) no longer enforces pod-spec rules on Pod UPDATE operations, preventing the Job controller from being permanently blocked when removing tracking finalizers from completed CronJob pods that predate the `automountServiceAccountToken` policy.

## [1.1.0] - 2026-05-04

### Added
- nvidia-imex package (GPU memory mapping over NVLink).
- libnvidia-nscq package (NVSwitch Configuration and Query library).
- DKMS build verification step in device-setup.
- 

### Changed
- NVIDIA 595 drivers — guest stack moves from 590 to 595 driver branch.
  All packages now use unversioned names from the CUDA repo exclusively
  (no Ubuntu restricted packages). DKMS compiles kernel modules at install
  time (no prebuilt linux-modules-nvidia-*-open). Requires nvidia-dkms-open.
- Single version pin: `nvidia_pkg_version` replaces `nvidia_pkg_release_ubuntu`
  and `nvidia_pkg_release_cuda` in group_vars.
- k3s bumped from `v1.33.7+k3s1` to `v1.35.4+k3s1`.
- CUDA version bumped from `13-0` to `13-2`.
- NVIDIA driver package version bumped from `595.58.03-1ubuntu1` to `595.71.05-1ubuntu1`.
- GPU operator helm chart bumped from `v24.9.2` to `v26.3.1`.
- `extract-acpi.sh`: firmware path now overridable via `$TDVF_FIRMWARE` env var (defaults to `firmware/TDVF.fd`). Allows testing `OVMF.inteltdx.ms.fd` without modifying the script.

### Fixed
-

### Removed
- nvidia-utils, nvidia-compute-utils, xserver-xorg-video-nvidia (folded into
  nvidia-driver-open / nvidia-open in 595).
- Prebuilt linux-modules-nvidia resolution and assertion (replaced by DKMS).
- nvidia_pkg_release_ubuntu, nvidia_pkg_release_cuda, nvidia_firmware_pkg
  variables (replaced by nvidia_pkg_version).
-

## [0.2.7] - 2026-03-31

### Added
- RTX Pro 6000 host-side support: profiles for supported Ubuntu releases, BAR-size
  checks, CC-mode verification via GPU tools, topology guidance.
- Guest profiles with vCPU support; fewer CPUs reserved on the host so more capacity
  goes to workloads.
- `setup-tdx-host --install-tools-only` for updating host dependencies and symlinks
  without a full host setup; enables `chutes-reset-gpus`.
- Monorepo package refactor: `src/` layout with `sek8s`, `sek8s-common`, and
  `attestation-proxy` as separate packages. VM image version moved to
  `ansible/guest/VERSION`. Python target aligned to 3.12.

### Changed
- NVIDIA 590 drivers — guest stack moves to the 590 driver / Fabric Manager line.
  Requires VBIOS 96.00.CF.00.xx or newer.
- Docker Hub rate-limit handling and TTL alignment fixes.
- Static manifests and both webhooks refresh more reliably; k3s vs Fabric Manager
  startup ordering tightened; storage mounts carry sync markers to reduce
  corruption risk on bad boots. Webhooks tuned for lower latency.

## [0.2.6] - 2026-03-25

### Fixed
- Updated nvidia-persistenced timeout configuration to prevent service startup
  failures.

## [0.2.5] - 2026-03-24

### Added
- Docker credentials support in the VM. Configure via config file or CLI; the VM
  handles sanitizing and setting up credentials for the admission controller and k3s.
- Pinned helm charts to the VM with automatic upgrade and signature verification on
  boot (no volume refresh required).

### Changed
- Memory arguments updated with numactl args and host-side NUMA config for
  performance optimization. Removed prealloc for shared memory pages (wasted host
  resources).
- VM resources always sync from the root volume so updates apply properly across
  versions.
- Certs and resources in k3s sync from static manifests (resolves SSL errors that
  previously required clearing the storage volume).
- Upgraded to latest 580 NVIDIA driver and Fabric Manager (stability fixes for TEE).

## [0.2.4] - 2026-03-18

### Fixed
- Updated VM launch RAM allocation to be based on GPU type. Previous flat allocation
  was a significant contributor to CUDA and memory issues.

## [0.2.3] - 2026-03-11

### Added
- Base image + overlay architecture with checksum verification in quick-launch.
  Prevents corruption-caused bad measurements and ensures VM + quick-launch version
  pairing matches expected measurements.
- Image management API: pre-populate images hosted by the validator; list, prune, and
  delete images via `chutes-miner tee image-[pull/list/delete/prune]`.
- Download option using aria2c for faster VM image downloads.
- Images moved out of the repo into `var/lib/chutes/`.

### Changed
- Network params tuned: reduced parallelism to address conntrack-related post-activation
  failures (~1-2% failure rate).
- Raw volumes now use XFS to allow for >16TB cache volumes. Existing ext4 volumes are
  backward compatible.

### Fixed
- Fixed a bug that prevented restarting the attestation-proxy in the attestation-system
  namespace (required VM restart in 0.2.1/0.2.2; resolved in 0.2.3 with normal kubectl).

## [0.2.2] - 2026-03-06

### Added
- Raw volume support. Existing qcow2 volumes continue to work; new volumes should use
  raw format.
- Updated cache cleaner image to check for GPU processes and VRAM threshold before
  cleaning.

### Changed
- System manager API updated to improve cache performance and avoid resource constraints
  during concurrent downloads.
- QEMU args updated to improve disk throughput (~50% improvement with raw volumes) and
  network performance.
- HF model download speed improved ~6-10x via system manager API updates.

### Fixed
- Fixed 500 errors from resource constraints during concurrent downloads in the system
  manager API.
