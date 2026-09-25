# Guest firmware provenance

The SEV-SNP launch measurement is a hash **of these firmware bytes**, and the TDX MRTD
covers its own. Both files are therefore pinned here and committed: firmware that changes
underneath a release silently invalidates every published measurement for it. Guest
firmware must never be resolved from `/usr/share/ovmf` at launch or at measurement time.

| File | Source | Reproduce with |
|---|---|---|
| `OVMF.inteltdx.fd` | edk2 `edk2-stable202605`, `OvmfPkg/IntelTdx/IntelTdxX64.dsc` | `./build-firmware.sh` |
| `OVMF.amdsev.fd` | edk2 `edk2-stable202605`, `OvmfPkg/AmdSev/AmdSevX64.dsc` + two deviations (below) | `./build-firmware.sh --amd-sev`, in the pinned image |

## OVMF.amdsev.fd

    sha256  6e64091ab2c139a4982c6fe7fd8736b7d6feb761022dcff7f78a2b82c1eb25b0
    edk2    edk2-stable202605 (b03a21a63e3bd001f52c527e5a57feddb53a690b)
    image   ubuntu:26.04@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78
    gcc     15.2.0
    target  RELEASE_GCC, built in the default EDK2_DIR (/tmp/edk2-tdvf-build)

Reproduce from the repo root:

    docker run --rm -v "$PWD/firmware:/fw" \
      ubuntu:26.04@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78 \
      /fw/build-firmware.sh --amd-sev

Verified reproducible: independent clean builds of this recipe produce identical bytes. Three
things are inputs to the digest besides the source, so keep all of them fixed:

- **The toolchain.** A different GCC yields different bytes; hence the pinned image.
- **The build directory.** Module paths under `EDK2_DIR` are embedded in the image. The same
  source and toolchain built in `/edk2` instead gives `c622fc41…`, not the digest above. The
  script warns when `EDK2_DIR` is overridden.
- **The two deviations below.** Each changes the bytes, so the launch measurement.

It must be `AmdSevX64.dsc`, not `OvmfPkgX64.dsc`: only the former includes
`BlobVerifierLibSevHashes`, which makes the firmware *enforce* the SNP kernel/initrd/cmdline
hashes rather than merely record them.

### Deviation 1: `PcdUse1GPageTable|TRUE`

Upstream `AmdSevX64.dsc` leaves it unset (checked on master, `edk2-stable202605` and
`edk2-stable202511`; `OvmfPkgX64.dsc` sets it). Without it `PlatformInitLib`
(`MemDetect.c`) clamps the guest physical address width to 40 bits, so any 64-bit PCI window
placed above 1 TiB falls outside the GCD memory map and `PciHostBridgeDxe` asserts. In a
RELEASE build that is a silent hang: one vCPU pegged, no serial output.

Guest RAM decides where the window lands, not the GPUs. With 768 GB of guest RAM the window
is placed at 1 TiB before a single BAR is assigned, so the Ubuntu binary this replaces could
not pass through even one 128 GB-BAR GPU. Reproduced on an 8x RTX PRO 6000 EPYC 7763 host: a
256 GiB window boots at [512G, 768G) and hangs at [1T, 1.25T). With the PCD set the firmware
keeps 46 address bits and sizes the MMIO window itself (8 TiB), so no
`opt/ovmf/X-PciMmio64Mb` hint is needed -- the same as the TDX firmware. Verified: all 8 GPUs
attach with 128G BAR2 assigned, the driver probes cleanly, CC mode on, 252 vCPUs, 768 GB.
Same fix as tinfoilsh/edk2#1 (tested there with 8x B300).

### Deviation 2: no embedded GRUB

`AmdSevX64.dsc` embeds a GRUB image for SEV's launch-secret LUKS flow. We direct-boot with
`kernel-hashes=on` and release the LUKS key from attestation in initramfs, so that GRUB never
runs. Building it (`OvmfPkg/AmdSev/Grub/grub.sh`) needs grub's `linuxefi.mod` and
`sevsecret.mod`, which Fedora/RHEL patch in and Debian/Ubuntu do not ship -- the reason
`--amd-sev` never completed here before. The script writes an empty `grub.efi` placeholder
instead. Beyond unblocking the build, this removes unused loader code from the measured
firmware and the only distro-package input to the digest; and a guest started without
`-kernel` has nothing to boot, so it fails closed rather than reaching an unverified loader.

### Replaced

`a2f54fb24af2aac3961e6c203afe08c795ca6fdd807cff9f7c8993e34d2d5e79`, the binary from Ubuntu's
`ovmf-amdsev` 2025.11-3ubuntu7, vendored because the source build did not complete. It has
neither deviation, so it hangs whenever the PCI window lands above 1 TiB. No SNP measurement
was ever published against it.

Check what is committed against a running platform:

    sha256sum firmware/OVMF.amdsev.fd
