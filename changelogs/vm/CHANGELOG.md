# Changelog

All notable changes to the VM / guest image will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

Version source of truth: `ansible/guest/VERSION`

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
