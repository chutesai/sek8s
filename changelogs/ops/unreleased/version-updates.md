### Added
- Layered guest image build caching: `prepared_img_path` (offline kernel install via `virt-customize`) and `gpu_img_path` (NVIDIA drivers + CUDA) checkpoint layers using `virsh suspend`/`resume`. K3s version bumps now resume from `gpu_img_path`, skipping the expensive NVIDIA driver reinstall.
- `save-checkpoint` Ansible role: reusable virsh suspend → disk copy → resume sequence; restarts `systemd-networkd` and `systemd-resolved` after resume to recover VM network state.
- `build-setup.yml` playbook: one-shot host preparation for VM image building — benchmarks regional apt mirrors and pins the fastest via the `host_mirror` role.
- Dynamic apt mirror selection in both host (`host_mirror` role) and guest (`common/mirror.yml`): benchmarks `archive.ubuntu.com` regional mirrors at build time; override by setting `apt_mirror` in inventory.
- `NO_CACHE` env var support: invalidates all cached image layers and forces a full rebuild from the base Ubuntu cloud image.

### Changed
- GPU role split from K3s-aware monolith: NVIDIA container runtime for K3s (`nvidia-runtime.yml`) moved into K3s role and runs conditionally when `nvidia-container-toolkit` is installed. GPU role now contains only driver/CUDA/InfiniBand setup.
- GPU checkpoint now saved after drivers only; K3s installs after the checkpoint. Previously K3s was baked into `gpu_img_path`, forcing driver reinstall on every K3s upgrade.
- Intermediate image paths (`prepared_img_path`, `gpu_img_path`) namespaced under `{{ img_dir }}/{{ build_env }}/{{ vm_version }}-*.qcow2` to prevent overwrites when managing multiple build versions on the same host.
- TDX host check in `quick-launch.sh` now checks sysfs (`/sys/module/kvm_intel/parameters/tdx=Y`) first, then `/proc/cpuinfo`, dmesg as last resort — avoids false negatives on long-running hosts where the dmesg ring buffer has rolled over.

### Fixed
-

### Removed
-
