### Fixed

- `setup_storage`: add `udevadm settle` before each `cryptsetup luksOpen` call and pass `--disable-locks` to prevent an indefinite hang on hosts with many passthrough PCI devices (8x H200 + NVSwitches). libdevmapper internally calls `udevadm settle` after creating a dm-crypt mapping; pre-draining the udev queue and disabling LUKS2 advisory file locking avoids blocking on the large backlog of GPU device-enumeration events that accumulate by init-bottom time.
