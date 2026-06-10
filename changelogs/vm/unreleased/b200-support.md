### Fixed
- Normalize PCI BDF addresses in gpu-verify to strip domain prefix and lowercase before comparison, fixing mismatches between sysfs and nvidia-smi formats (e.g. `0000:a1:00.0` vs `00000000:A1:00.0`)
