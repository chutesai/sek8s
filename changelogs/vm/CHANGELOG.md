# Changelog

All notable changes to the VM / guest image will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

Version source of truth: `ansible/guest/VERSION`

## [1.3.1] - 2026-06-15

### Added
- `libnvidia-gpucomp` and `nvidia-persistenced` packages to guest NVIDIA driver install (required by B300 driver stack).
- `nvidia-modprobe` package to DKMS kernel module install (fixes module load on boot).
- NVIDIA apt version pin (`/etc/apt/preferences.d/nvidia-version-pin`, `Pin-Priority: 1001`) locking all `nvidia-*`/`libnvidia-*` packages to `nvidia_pkg_version` so apt cannot pull a mismatched driver build.

### Changed
- System manager env: replaced `HF_HUB_DISABLE_XET=1` + `HF_HUB_ENABLE_HF_TRANSFER=1` with throttled XET tuning (`HF_XET_FIXED_DOWNLOAD_CONCURRENCY=16`, `TOKIO_WORKER_THREADS=8`)
- OPA admission policy: added `HF_XET_FIXED_DOWNLOAD_CONCURRENCY` and `TOKIO_WORKER_THREADS` to allowed pod env vars
- Admission controller source repository moved from `rayonlabs/sek8s` to `chutesai/sek8s` (`admission_controller_repo` default).
- LUKS build dependencies (`cryptsetup`, `dhcpcd-base`, `openssl`, `xfsprogs`, `e2fsprogs`) are now installed via `system_packages` during base image setup instead of a late `chroot apt-get install` in `luks_encrypt.yml`, removing a network-dependent install step from the encryption stage. Packages still land in the final image via the rootfs backup/restore.

### Fixed
- Fix LUKS key confirmation on first boot: freshly provisioned volumes now set the KEY_ADDED flag so confirm_rotation sends rotated=true, preventing the API from discarding the applied passphrase and bricking the volume on subsequent boots
- Normalize PCI BDF addresses in gpu-verify to strip domain prefix and lowercase before comparison, fixing mismatches between sysfs and nvidia-smi formats (e.g. `0000:a1:00.0` vs `00000000:A1:00.0`)
- System manager no longer crash-loops waiting for k3s: made `ReadOnlyPaths=/run/k3s/containerd` optional so the service starts immediately on boot even if k3s hasn't created the socket yet
- Attestation proxy startup probe: increased tolerance from 65s to 310s as a safety net for slow boots
- k3s boot ordering: added `After=attestation-service.service` so the attestation-proxy pod isn't scheduled before the host attestation socket and TLS certs exist
- attestation-proxy now self-heals after an ungraceful reboot. A new every-boot cluster-init script (`05-attestation-proxy-recovery.sh`, ordered before the `99-purge-kubeconfig.sh` admin-kubeconfig purge) force-deletes any attestation-proxy DaemonSet pod left in an `Unknown`/`Failed` phase or with `reason=NodeLost` — states the DaemonSet controller will not replace on its own — so the proxy is recreated automatically instead of staying down until a manual `kubectl rollout restart`. Running/Pending pods are left untouched, and the script always exits 0 so it can never trigger the cluster-init power-off.
- system-manager hf-xet cache directory (`/var/snap/cache/.xdg-cache`) is now created reliably at runtime via a dedicated systemd drop-in (`system-manager.service.d/cache-volume.conf`). The `ExecStartPre` uses the `+` prefix to run outside the unit sandbox so the chown to `system-manager:tdx` has `CAP_CHOWN`, and `ReadWritePaths=/var/snap/cache` is scoped to the drop-in since the cache volume only exists at runtime, not during image build.
- attestation-proxy no longer fails its startup health probe after a reboot. The proxy's `/health` returns 503 until the attestation Unix socket (`/run/attestation-service/attestation.sock`) is bound, but that socket is created by the host `attestation-service` — a `Type=simple` unit, so systemd considers it "started" at process fork, ~2s before uvicorn actually binds the socket. k3s only orders `After=` that unit, so the proxy could start in the gap and churn on the failing probe (surfacing as `Unknown`/`Completed` pods after reboot). The proxy DaemonSet now has a `wait-for-attestation-socket` init container that blocks on the real socket file before the main container starts.
- Debug-build secrets encryption now actually works (it was silently off). Three defects compounded:
  - The baked debug key base64-decoded to **44 bytes**; secretbox requires exactly **32**, so the apiserver rejected it (`got 44, expected one of [32]`) whenever encryption was actually wired up. Replaced with a valid 32-byte key.
  - `k3s-config-init.sh` checked for `/run/chutes/k3s-encryption-config.yaml` to decide whether to add `encryption-provider-config`, but on debug builds that file was only copied later by k3s.service's `ExecStartPre` (after config-init), so the arg was never added and the apiserver ran without encryption. config-init now materializes the debug key from `/etc/chutes/` into `/run/chutes/` before its own check, matching the boot stage at which prod's initramfs provides it.
  - `00-reencrypt-secrets.sh` could mark itself done without actually encrypting anything — it ran the destructive kine purge and touched its run-once marker even when the apiserver was unreachable (rewrite a vacuous no-op) or not actually encrypting. Since secrets encryption is mandatory as of the current VM version, it now treats any unencrypted condition as a **hard failure with no marker** (escalating to the cluster-init power-off) instead of silently skipping: it requires the apiserver to be reachable, requires `encryption-provider-config` to be wired into the apiserver config (not just the file to exist) and to use secretbox, and verifies every secret is encrypted at rest before purging or marking. The completion marker is also now **self-validating** — it is honored only when secrets are verified encrypted at rest, so a stale marker from an earlier false-success run clears itself and re-runs rather than permanently skipping re-encryption.
- Pods (notably attestation-proxy) no longer get wedged after an ungraceful shutdown/reboot. k3s runs `KillMode=process` and the shutdown backstop force-kills `containerd-shim` to free the storage bind mount, so pods were torn down without the kubelet removing their sandboxes; because containerd's metadata lives on the persistent cache volume, those half-killed sandbox records survived reboot and accumulated, leaving the kubelet holding multiple sandboxes for one pod (a split-brain it never converges). Two changes address this:
  - **Graceful node shutdown** — the kubelet now drains pods on shutdown via a `shutdownGracePeriod` KubeletConfiguration drop-in (written by `k3s-config-init.sh` into the runtime `kubelet.conf.d`, since `/etc/rancher/k3s` is an initially-empty bind mount), paired with a `logind` `InhibitDelayMaxSec` drop-in so the shutdown inhibitor honours the drain window. Clean shutdowns now remove sandboxes properly instead of force-killing shims.
  - **Boot-time cleanup** — `k3s-config-init.sh` (the existing pre-k3s hook) now clears leftover CNI IPAM state, bridge/vxlan interfaces, and kubelet pod mounts before k3s starts, so the kubelet's sandbox GC can reconcile cleanly after a crash or force-kill. It preserves the containerd image content store (no re-pull) and is best-effort (guarded so it can never fail config generation). The shutdown `pkill containerd-shim` backstop is retained as a no-op-in-the-common-case safety net.
- Pinned `nvlsm` to `2025.10.12-1` in the InfiniBand setup (`gpu` role) via the new `nvlsm_version` var. NVIDIA's CUDA repo can publish a `Packages` index entry for a newer `nvlsm` before uploading the matching `.deb`, so the unpinned install resolved a candidate version that 404'd, failing the guest image build. Bump `nvlsm_version` to the newest version whose `.deb` resolves when updating.

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
