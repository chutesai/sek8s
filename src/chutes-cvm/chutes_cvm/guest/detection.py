"""PCI device discovery for GPU, NVSwitch, and InfiniBand devices.

Most functions are self-contained: they read lspci / sysfs / nvidia-gpu-tools
output and return BDF lists or model mappings without modifying system state.
"""

import os
import platform
import re

from chutes_cvm import proc

_NVIDIA_VENDOR = "10de"
_MELLANOX_VENDOR = "15b3"


# The TDX Module's fixed leaf-1 CPUID EDX baseline. On a bare launch host the raw
# host EDX differs (masked bits like PSE36), but the GUEST — and thus the SMBIOS
# Type-4 Processor ID that lands in RTMR0 — always sees this constant, so we combine
# it with the host's native leaf-1 EAX (family/model/stepping, which TDX passes
# through) to reconstruct the guest processor_id host-side. Same value discover-
# profile.sh uses; re-verify only if the TDX Module version changes.
_TDX_LEAF1_EDX = 0x1FA9FBFF


# Expected QEMU per Ubuntu release (each ships one build). Upstream version only;
# distro "+ds-...ubuntuX.Y" SRU revisions do not move RTMR0.
SUPPORTED_QEMU_BY_OS = {
    "26.04": "10.2.1",
}


# The guest ``-cpu`` args every supported host launches with. ``host`` passes the host CPU
# model through; ``-avx10`` masks the AVX10 feature off. -cpu shapes the CPUID leaves the
# guest sees, so this must be one value shared by the launcher
# (``chutes_cvm.guest.__main__``), the pre-upgrade profile rewrite (``guest.preflight``) and
# offline measurement (``measurement.topology_spec``) — a divergence moves RTMR0.
# It was OS-gated while 24.04 was supported (its QEMU 8.2 has no ``avx10`` property to mask);
# every release in SUPPORTED_QEMU_BY_OS takes the mask, so it is now a constant.
GUEST_CPU_ARGS = "host,-avx10"


def detect_os_version() -> str | None:
    """Return the host OS VERSION_ID (e.g. '26.04') from /etc/os-release, or None."""
    try:
        return platform.freedesktop_os_release().get("VERSION_ID")
    except (OSError, AttributeError):
        return None


def detect_qemu_version() -> str | None:
    """Return the host qemu-system-x86_64 upstream version (e.g. '10.2.1'), or None."""
    try:
        out = proc.run(
            ["qemu-system-x86_64", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, proc.TimeoutExpired, OSError):
        return None
    match = re.search(r"version (\d+(?:\.\d+)*)", out.stdout)
    return match.group(1) if match else None


def verify_host_qemu_supported() -> None:
    """Raise ValueError unless the host runs its OS release's expected QEMU.

    QEMU generates the guest ACPI measured into RTMR0, so a mismatched QEMU
    attests with an RTMR0 we have no measurement for. Operator-facing pre-flight,
    not a security boundary (the real gate is the control-plane RTMR0 match).
    """
    qemu_version = detect_qemu_version()
    if qemu_version is None:
        raise ValueError(
            "Could not determine the host QEMU version "
            "(`qemu-system-x86_64 --version`). Install qemu-system-x86 and retry."
        )
    os_version = detect_os_version()
    if os_version is None:
        raise ValueError(
            "Could not determine the host OS release (/etc/os-release); "
            "cannot verify the expected QEMU version."
        )
    expected = SUPPORTED_QEMU_BY_OS.get(os_version)
    if expected is None:
        raise ValueError(
            f"Host OS release {os_version!r} is not supported "
            f"{list(SUPPORTED_QEMU_BY_OS)}. Supported releases ship a QEMU whose "
            f"RTMR0 is baselined, so this host cannot attest until it is upgraded. To "
            f"register this host's class ahead of that upgrade, run `chutes-cvm host "
            f"submit-profile --target-os {sorted(SUPPORTED_QEMU_BY_OS)[-1]}`."
        )
    if qemu_version != expected:
        raise ValueError(
            f"Host OS {os_version} ships (and we baseline) QEMU {expected}, but "
            f"found QEMU {qemu_version}. A different QEMU generates different guest "
            f"ACPI tables → a different TDX RTMR0 → rejected at attestation. Install "
            f"the release's QEMU (`sudo apt update && sudo apt full-upgrade`). If "
            f"{os_version} itself has moved to QEMU {qemu_version}, report it to Chutes "
            f"— the new build has to be baselined before any host on it can attest."
        )


# NVSwitch device ID (H100/H200 multi-GPU systems)
_PCI_DEVICE_NVSWITCH = "22a3"


def _lspci_lines(vendor: str) -> list[str]:
    """Return lspci -Dnn lines matching the given PCI vendor ID.

    -D ensures BDFs are always in full domain form (0000:bb:dd.f),
    matching nvidia-gpu-tools output and sysfs expectations.
    """
    output = proc.check_output(["lspci", "-Dnn"], stderr=proc.STDOUT)
    return [line for line in output.decode().splitlines() if vendor in line]


# PCI class 0207 = InfiniBand controller. Excludes Ethernet [0200], DMA [0801], etc.
_PCI_CLASS_INFINIBAND = "0207"


def _is_vf(bdf: str) -> bool:
    """Return True if device is an SR-IOV Virtual Function (has physfn)."""
    physfn = f"/sys/bus/pci/devices/{bdf}/physfn"
    return os.path.exists(physfn)


def detect_infiniband_vfs(pf_bdfs: list[str]) -> list[str]:
    """Return VF BDFs whose Physical Function is in pf_bdfs."""
    pf_set = set(pf_bdfs)
    vfs = []
    for line in _lspci_lines(_MELLANOX_VENDOR):
        parts = line.strip().split()
        if not parts:
            continue
        if f"[{_PCI_CLASS_INFINIBAND}]" not in line:
            continue
        bdf = parts[0]
        if not _is_vf(bdf):
            continue
        try:
            physfn_path = os.path.realpath(f"/sys/bus/pci/devices/{bdf}/physfn")
            pf_bdf = os.path.basename(physfn_path)
            if pf_bdf in pf_set:
                vfs.append(bdf)
        except OSError:
            continue
    return sorted(vfs)


# ---------------------------------------------------------------------------
# Full topology detection and profile matching
# ---------------------------------------------------------------------------
