#!/usr/bin/env bash
set -euo pipefail

# Build guest firmware from edk2 source.
#
# Outputs directly to firmware/ so the result can be committed. Both platforms measure
# their firmware -- TDX through MRTD, SEV-SNP through the launch digest -- so the bytes
# that ship here are the bytes every published measurement is computed against.
#
# Usage:
#   ./build-firmware.sh                  # Config-B → firmware/OVMF.inteltdx.fd
#   ./build-firmware.sh --secure-boot    # Config-A → firmware/OVMF.inteltdx.ms.fd
#   ./build-firmware.sh --amd-sev        # AmdSevX64 → firmware/OVMF.amdsev.fd
#
# The toolchain is part of the output: a different GCC yields different bytes, and so a
# different measurement. Build in the pinned image PROVENANCE.md records, e.g.
#   docker run --rm -v "$PWD/firmware:/fw" ubuntu:26.04@sha256:<digest> /fw/build-firmware.sh --amd-sev

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDK2_TAG="edk2-stable202605"
EDK2_COMMIT="b03a21a63e3bd001f52c527e5a57feddb53a690b"
EDK2_DIR="${EDK2_DIR:-/tmp/edk2-tdvf-build}"
SECURE_BOOT=0
AMD_SEV=0

for arg in "$@"; do
    case "$arg" in
        --secure-boot) SECURE_BOOT=1 ;;
        --amd-sev) AMD_SEV=1 ;;
        --help|-h)
            echo "Usage: $0 [--secure-boot | --amd-sev]"
            echo ""
            echo "  --secure-boot   Build Config-A with Microsoft Secure Boot keys"
            echo "  --amd-sev       Build AmdSevX64.dsc -> firmware/OVMF.amdsev.fd"
            echo ""
            echo "Without flags, builds Config-B (IntelTdxX64.dsc) without Secure Boot."
            echo "Output lands in firmware/ ready to commit. The SEV-SNP launch digest and"
            echo "the TDX MRTD are hashes of these bytes, so compare the printed sha256"
            echo "against firmware/PROVENANCE.md before replacing a committed file."
            echo ""
            echo "Environment:"
            echo "  EDK2_DIR        Build directory (default: /tmp/edk2-tdvf-build). Changing it"
            echo "                  changes the output bytes -- keep the default to reproduce."
            exit 0
            ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

# --- Install build prerequisites ---
PACKAGES=(uuid-dev nasm iasl build-essential git python3)
if [[ $SECURE_BOOT -eq 1 ]]; then
    PACKAGES+=(python3-virt-firmware)
fi

# Root in the build container has no sudo, and needs none.
SUDO=""
if [[ $(id -u) -ne 0 ]]; then SUDO="sudo"; fi

echo "--- Installing build prerequisites ---"
${SUDO} apt-get update -qq
DEBIAN_FRONTEND=noninteractive ${SUDO} apt-get install -y -qq "${PACKAGES[@]}"

if [[ "${EDK2_DIR}" != "/tmp/edk2-tdvf-build" ]]; then
    # Module paths under the build directory end up in the image, so the same source built
    # elsewhere is different bytes -- a different measurement, not a reproduction.
    echo "WARNING: EDK2_DIR=${EDK2_DIR} is not the default; the output will NOT match the" >&2
    echo "         digests in PROVENANCE.md even though the source is identical." >&2
fi

echo "=== Building TDVF firmware from ${EDK2_TAG} ==="
echo "    Secure Boot: $([ $SECURE_BOOT -eq 1 ] && echo 'yes' || echo 'no')"
echo "    Build dir:   ${EDK2_DIR}"
echo ""

if [[ ! -d "${EDK2_DIR}/.git" ]]; then
    echo "--- Cloning edk2 ---"
    git clone https://github.com/tianocore/edk2.git "${EDK2_DIR}"
fi

cd "${EDK2_DIR}"
# An earlier --amd-sev run leaves its .dsc edit and source patches in this reused tree;
# drop them so the checkout below is clean and every edit is applied to pristine upstream
# every time -- and so a later TDX build never compiles AMD-only patches.
git checkout -- OvmfPkg
git fetch --tags
git checkout "${EDK2_TAG}"
# A tag can be moved; the commit is what PROVENANCE.md and every measurement record.
if [[ "$(git rev-parse HEAD)" != "${EDK2_COMMIT}" ]]; then
    echo "ERROR: ${EDK2_TAG} resolves to $(git rev-parse HEAD), expected ${EDK2_COMMIT}" >&2
    exit 1
fi
git submodule update --init --recursive

echo "--- Building BaseTools ---"
make -C BaseTools -j"$(nproc)"

export PYTHON_COMMAND=python3
set +u
# edksetup.sh parses "$@", and a sourced script inherits its caller's positional
# parameters -- so any flag given to THIS script (--amd-sev, --secure-boot) reaches
# edksetup as an unknown option, whereupon it prints usage and returns WITHOUT
# configuring the build environment, and the build below fails obscurely. Clear them
# first. (Only the no-argument default ever worked before this.)
set --
source ./edksetup.sh
set -u

if [[ $AMD_SEV -eq 1 ]]; then
    # It must be AmdSevX64.dsc and not OvmfPkgX64.dsc: only the former pulls in
    # BlobVerifierLibSevHashes, which is what makes the firmware VERIFY the loaded
    # kernel/initrd against the SNP hashes page. OvmfPkgX64 uses BlobVerifierLibNull and
    # would load whatever QEMU hands it, turning kernel-hashes=on from an enforced
    # guarantee into a recorded intention.
    #
    # Every deviation from upstream is explained in PROVENANCE.md:
    #
    # * No embedded GRUB. That GRUB exists only for the SEV launch-secret LUKS flow; we
    #   direct-boot with kernel-hashes=on and take the LUKS key from attestation in
    #   initramfs, so it never runs. Building it needs grub's linuxefi.mod and
    #   sevsecret.mod, which Fedora/RHEL patch in and Debian/Ubuntu do not ship. An empty
    #   placeholder lets the .fdf resolve the file; a guest started without -kernel then
    #   has nothing to boot and fails closed, rather than reaching an unverified loader.
    : > OvmfPkg/AmdSev/Grub/grub.efi

    # * Source patches (patches/amdsev/, the source of truth; see PROVENANCE.md): 1G page
    #   tables for large PCI windows, then boot restricted to the measured kernel and an
    #   ACPI integrity check.
    #   Applied in name order; a patch that no longer applies fails the build rather than
    #   being skipped -- git apply does no fuzzy matching.
    for patch in "${SCRIPT_DIR}"/patches/amdsev/*.patch; do
        echo "--- Applying $(basename "${patch}") ---"
        git apply --whitespace=nowarn "${patch}"
    done

    echo "--- Building AmdSevX64.dsc ---"
    build -p OvmfPkg/AmdSev/AmdSevX64.dsc -a X64 -t GCC -b RELEASE

    DEST="${SCRIPT_DIR}/OVMF.amdsev.fd"
    cp "${EDK2_DIR}/Build/AmdSev/RELEASE_GCC/FV/OVMF.fd" "${DEST}"

    # A byte difference here is a DIFFERENT launch measurement, so the digest is the
    # thing to check -- not whether the guest happens to boot.
    echo ""
    echo "!!! Compare against firmware/PROVENANCE.md before replacing the committed file:"
    echo "    built   $(sha256sum "${DEST}" | awk '{print $1}')"
elif [[ $SECURE_BOOT -eq 0 ]]; then
    echo "--- Building Config-B (IntelTdxX64.dsc, no Secure Boot) ---"
    build -p OvmfPkg/IntelTdx/IntelTdxX64.dsc -a X64 -t GCC -b RELEASE

    DEST="${SCRIPT_DIR}/OVMF.inteltdx.fd"
    cp "${EDK2_DIR}/Build/IntelTdx/RELEASE_GCC/FV/OVMF.fd" "${DEST}"
else
    echo "--- Building Config-A (OvmfPkgX64.dsc, Secure Boot) ---"
    build -p OvmfPkg/OvmfPkgX64.dsc -a X64 -t GCC -b RELEASE \
        -D CC_MEASUREMENT_ENABLE=TRUE \
        -D SECURE_BOOT_ENABLE=TRUE \
        -D FD_SIZE_4MB

    DEST="${SCRIPT_DIR}/OVMF.inteltdx.ms.fd"
    cp "${EDK2_DIR}/Build/OvmfX64/RELEASE_GCC/FV/OVMF.fd" "${DEST}"

    echo "--- Enrolling Microsoft Secure Boot keys ---"
    virt-fw-vars \
        --input "${DEST}" \
        --output "${DEST}" \
        --enroll-cert "Microsoft" \
        --secure-boot
fi

echo ""
echo "=== Firmware built ==="
echo "    Output: ${DEST}"
echo "    Size:   $(wc -c < "${DEST}") bytes"
echo "    SHA256: $(sha256sum "${DEST}" | awk '{print $1}')"
echo "    Source: ${EDK2_TAG} ($(git rev-parse HEAD))"
echo "    GCC:    $(gcc -dumpfullversion)"
echo ""
echo "    Ready to commit: git add ${DEST}"
