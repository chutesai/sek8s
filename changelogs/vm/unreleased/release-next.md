### Added

- `guest-tools/scripts/compute-rtmr3.sh`: compute the expected RTMR3 at build time by mounting the final qcow2 read-only with `guestmount` and simulating the exact SHA-384 extension chain from `rtmr3-measure`. Eliminates the need to boot twice just to capture RTMR3 — the Ansible build runs this automatically and writes `<image>.rtmr3` alongside the qcow2 before the LUKS step.
- `ansible/guest/playbooks/chutes-miner-vm.yml`, `tee-gpu-vm.yml`: add `compute-rtmr3` play that runs `compute-rtmr3.sh` automatically after `finalize-vm-image` and before `luks`/`prime-vm`, writing the expected RTMR3 to `<final_img_path>.rtmr3`.

### Fixed

- `setup_storage`: add `udevadm settle` before each `cryptsetup luksOpen` call and pass `--disable-locks` to prevent an indefinite hang on hosts with many passthrough PCI devices (8x H200 + NVSwitches). libdevmapper internally calls `udevadm settle` after creating a dm-crypt mapping; pre-draining the udev queue and disabling LUKS2 advisory file locking avoids blocking on the large backlog of GPU device-enumeration events that accumulate by init-bottom time.
