### Changed
- NVIDIA 595 drivers — guest stack moves from 590 to 595 driver branch.
  All packages now use unversioned names from the CUDA repo exclusively
  (no Ubuntu restricted packages). DKMS compiles kernel modules at install
  time (no prebuilt linux-modules-nvidia-*-open). Requires nvidia-dkms-open.
- Single version pin: `nvidia_pkg_version` replaces `nvidia_pkg_release_ubuntu`
  and `nvidia_pkg_release_cuda` in group_vars.

### Added
- nvidia-imex package (GPU memory mapping over NVLink).
- libnvidia-nscq package (NVSwitch Configuration and Query library).
- DKMS build verification step in device-setup.

### Removed
- nvidia-utils, nvidia-compute-utils, xserver-xorg-video-nvidia (folded into
  nvidia-driver-open / nvidia-open in 595).
- Prebuilt linux-modules-nvidia resolution and assertion (replaced by DKMS).
- nvidia_pkg_release_ubuntu, nvidia_pkg_release_cuda, nvidia_firmware_pkg
  variables (replaced by nvidia_pkg_version).
