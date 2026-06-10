#!/usr/bin/env bash
set -euo pipefail

# Build TDVF firmware from edk2 source for TDX VMs.
#
# Outputs directly to firmware/ so the result can be committed.
#
# Usage:
#   ./build-firmware.sh                  # Config-B → firmware/OVMF.inteltdx.fd
#   ./build-firmware.sh --secure-boot    # Config-A → firmware/OVMF.inteltdx.ms.fd
#
# Prerequisites:
#   apt install uuid-dev nasm iasl build-essential python3-distutils git
#   pip install virt-firmware   (only needed for --secure-boot)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDK2_TAG="edk2-stable202605"
EDK2_DIR="${EDK2_DIR:-/tmp/edk2-tdvf-build}"
SECURE_BOOT=0

for arg in "$@"; do
    case "$arg" in
        --secure-boot) SECURE_BOOT=1 ;;
        --help|-h)
            echo "Usage: $0 [--secure-boot]"
            echo ""
            echo "  --secure-boot   Build Config-A with Microsoft Secure Boot keys"
            echo "                  (requires python3 virt-firmware package)"
            echo ""
            echo "Without flags, builds Config-B (IntelTdxX64.dsc) without Secure Boot."
            echo "Output lands in firmware/ ready to commit."
            echo ""
            echo "Environment:"
            echo "  EDK2_DIR        Build directory (default: /tmp/edk2-tdvf-build)"
            exit 0
            ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

echo "=== Building TDVF firmware from ${EDK2_TAG} ==="
echo "    Secure Boot: $([ $SECURE_BOOT -eq 1 ] && echo 'yes' || echo 'no')"
echo "    Build dir:   ${EDK2_DIR}"
echo ""

if [[ ! -d "${EDK2_DIR}/.git" ]]; then
    echo "--- Cloning edk2 ---"
    git clone https://github.com/tianocore/edk2.git "${EDK2_DIR}"
fi

cd "${EDK2_DIR}"
git fetch --tags
git checkout "${EDK2_TAG}"
git submodule update --init --recursive

echo "--- Building BaseTools ---"
make -C BaseTools -j"$(nproc)"

source ./edksetup.sh

if [[ $SECURE_BOOT -eq 0 ]]; then
    echo "--- Building Config-B (IntelTdxX64.dsc, no Secure Boot) ---"
    build -p OvmfPkg/IntelTdx/IntelTdxX64.dsc -a X64 -t GCC5 -b RELEASE

    DEST="${SCRIPT_DIR}/OVMF.inteltdx.fd"
    cp "${EDK2_DIR}/Build/IntelTdx/RELEASE_GCC5/FV/OVMF.fd" "${DEST}"
else
    echo "--- Building Config-A (OvmfPkgX64.dsc, Secure Boot) ---"
    build -p OvmfPkg/OvmfPkgX64.dsc -a X64 -t GCC5 -b RELEASE \
        -D CC_MEASUREMENT_ENABLE=TRUE \
        -D SECURE_BOOT_ENABLE=TRUE \
        -D FD_SIZE_4MB

    DEST="${SCRIPT_DIR}/OVMF.inteltdx.ms.fd"
    cp "${EDK2_DIR}/Build/OvmfX64/RELEASE_GCC5/FV/OVMF.fd" "${DEST}"

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
echo ""
echo "    Ready to commit: git add ${DEST}"
