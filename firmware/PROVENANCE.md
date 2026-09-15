# Guest firmware provenance

The SEV-SNP launch measurement is a hash **of these firmware bytes**, and the TDX MRTD
covers its own. Both files are therefore pinned here and committed: firmware that changes
underneath a release silently invalidates every published measurement for it. Guest
firmware must never be resolved from `/usr/share/ovmf` at launch or at measurement time.

| File | Source | Reproduce with |
|---|---|---|
| `OVMF.inteltdx.fd` | edk2 `edk2-stable202605`, `OvmfPkg/IntelTdx/IntelTdxX64.dsc` | `./build-firmware.sh` |
| `OVMF.amdsev.fd` | Ubuntu `ovmf-amdsev` 2025.11-3ubuntu7 (edk2 `OvmfPkg/AmdSev/AmdSevX64.dsc`) | `./build-firmware.sh --amd-sev` |

## OVMF.amdsev.fd

    sha256  a2f54fb24af2aac3961e6c203afe08c795ca6fdd807cff9f7c8993e34d2d5e79

Vendored from the distro package rather than built here, because `AmdSevX64.dsc` also
requires the embedded-GRUB sub-build. `--amd-sev` reproduces it from source; **confirm the
digest matches before swapping the file**, since a different build yields a different
launch measurement even when functionally identical.

Check what is committed against a running platform:

    sha256sum firmware/OVMF.amdsev.fd
