"""PCI device discovery for GPU, NVSwitch, and InfiniBand devices.

All functions are side-effect-free: they read lspci / nvidia-gpu-tools output
and return BDF lists or model mappings without modifying system state.
"""

import os
import re
import subprocess

from chutes.guest.gpu.profiles import GPU_PROFILES
from chutes.guest.gpu.tools import ensure_gpu_tools_available

_NVIDIA_VENDOR = '10de'
_MELLANOX_VENDOR = '15b3'

# NVSwitch device ID (H100/H200 multi-GPU systems)
_PCI_DEVICE_NVSWITCH = '22a3'


def _extract_device_id(lspci_line: str, vendor: str = '10de') -> str | None:
    """Extract PCI device ID from lspci line, e.g. [10de:2901] -> 2901."""
    match = re.search(rf'\[{vendor}:([0-9a-f]{{4}})\]', lspci_line, re.IGNORECASE)
    return match.group(1).lower() if match else None


def _lspci_lines(vendor: str) -> list[str]:
    """Return lspci -Dnn lines matching the given PCI vendor ID.

    -D ensures BDFs are always in full domain form (0000:bb:dd.f),
    matching nvidia-gpu-tools output and sysfs expectations.
    """
    output = subprocess.check_output(["lspci", "-Dnn"], stderr=subprocess.STDOUT)
    return [line for line in output.decode().splitlines() if vendor in line]


def _match_gpu_model(lspci_line: str) -> str | None:
    """Return the GPU_PROFILES key for an lspci line, or None.

    Uses PCI device ID only; each profile's matches_device_id checks pci_device_ids.
    """
    device_id = _extract_device_id(lspci_line, _NVIDIA_VENDOR)
    if not device_id:
        return None
    for name, profile in GPU_PROFILES.items():
        if profile.matches_device_id(device_id):
            return name
    return None


def detect_nvidia_gpus() -> list[str]:
    """Detect NVIDIA GPU BDFs via lspci (vendor 10de, device IDs from GpuProfile.pci_device_ids)."""
    devices = []
    for line in _lspci_lines(_NVIDIA_VENDOR):
        parts = line.strip().split()
        if not parts:
            continue
        if _match_gpu_model(line) is not None:
            devices.append(parts[0])
    return sorted(devices)


def get_gpu_bdfs() -> list[str] | None:
    """Get GPU BDFs from nvidia-gpu-tools --query-cc-mode.

    Returns None if the tool is unavailable or returns no GPUs.
    """
    try:
        cmd = ensure_gpu_tools_available()
        out = subprocess.run(
            [cmd, '--query-cc-mode'],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode != 0:
            return None
        bdf_re = re.compile(
            r'\s+\d+\s+GPU\s+([0-9a-f]{4}:[0-9a-f]{2,4}:[0-9a-f]{2}\.[0-9])',
            re.IGNORECASE,
        )
        bdfs = []
        for line in (out.stdout or '').splitlines():
            m = bdf_re.search(line)
            if m:
                bdfs.append(m.group(1))
        return sorted(bdfs) if bdfs else None
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, RuntimeError):
        return None


def detect_nvswitches() -> list[str]:
    """Detect NVSwitch BDFs via lspci (vendor 10de, device ID 22a3)."""
    devices = []
    for line in _lspci_lines(_NVIDIA_VENDOR):
        parts = line.strip().split()
        if not parts:
            continue
        device_id = _extract_device_id(line, _NVIDIA_VENDOR)
        if device_id == _PCI_DEVICE_NVSWITCH:
            devices.append(parts[0])
    return sorted(devices)


def get_gpu_models_from_lspci(bdfs: list[str]) -> dict[str, str]:
    """Map each GPU BDF to its GPU_PROFILES key (or 'default') via lspci."""
    bdf_set = set(bdfs)
    result = {}
    for line in _lspci_lines(_NVIDIA_VENDOR):
        parts = line.strip().split()
        if not parts:
            continue
        bdf = parts[0]
        if bdf not in bdf_set:
            continue
        result[bdf] = _match_gpu_model(line) or 'default'
    return result


# PCI class 0207 = InfiniBand controller. Excludes Ethernet [0200], DMA [0801], etc.
_PCI_CLASS_INFINIBAND = '0207'


def _is_vf(bdf: str) -> bool:
    """Return True if device is an SR-IOV Virtual Function (has physfn)."""
    physfn = f'/sys/bus/pci/devices/{bdf}/physfn'
    return os.path.exists(physfn)


def detect_cx7_bridge_pfs() -> list[str]:
    """Detect ConnectX-7 NVSwitch bridge Physical Function BDFs via VPD.

    On B200 HGX systems the NVSwitch ASICs are not visible as PCIe devices.
    They are managed through ConnectX-7 bridge PFs whose Vital Product Data
    contains the field ``SMDL=SW_MNG``.  These PFs must stay on the host for
    Fabric Manager to communicate with the NVSwitches over InfiniBand; they
    must never be bound to vfio-pci or passed through to a guest.

    Regular ConnectX-7 NIC PFs share the same PCI device ID (15b3:1021) but
    their VPD does not contain ``SMDL=SW_MNG``, so this function correctly
    returns only the bridge PFs.

    Returns an empty list on non-B200 hosts (VPD read is cheap; no NVSwitch
    bridge PFs will have the marker).
    """
    bridge_pfs = []
    for line in _lspci_lines(_MELLANOX_VENDOR):
        parts = line.strip().split()
        if not parts:
            continue
        if f'[{_PCI_CLASS_INFINIBAND}]' not in line:
            continue
        bdf = parts[0]
        if _is_vf(bdf):
            continue
        try:
            result = subprocess.run(
                ["lspci", "-vv", "-s", bdf],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if "SMDL=SW_MNG" in result.stdout:
                bridge_pfs.append(bdf)
        except (subprocess.TimeoutExpired, OSError):
            continue
    return sorted(bridge_pfs)


def detect_infiniband_pfs(exclude_bdfs: list[str] | None = None) -> list[str]:
    """Detect InfiniBand Physical Function BDFs (vendor 15b3, class 0207, not VF).

    PFs stay bound to mlx5_core on the host; we create VFs from them for passthrough.

    ``exclude_bdfs`` is an optional list of BDFs to omit from the result.  Pass
    the output of :func:`detect_cx7_bridge_pfs` here to ensure NVSwitch bridge
    PFs are never included in the passthrough device list.
    """
    excluded = set(exclude_bdfs) if exclude_bdfs else set()
    devices = []
    for line in _lspci_lines(_MELLANOX_VENDOR):
        parts = line.strip().split()
        if not parts:
            continue
        if f'[{_PCI_CLASS_INFINIBAND}]' not in line:
            continue
        bdf = parts[0]
        if _is_vf(bdf):
            continue
        if bdf in excluded:
            continue
        devices.append(bdf)
    return sorted(devices)


def detect_infiniband_vfs(pf_bdfs: list[str]) -> list[str]:
    """Return VF BDFs whose Physical Function is in pf_bdfs."""
    pf_set = set(pf_bdfs)
    vfs = []
    for line in _lspci_lines(_MELLANOX_VENDOR):
        parts = line.strip().split()
        if not parts:
            continue
        if f'[{_PCI_CLASS_INFINIBAND}]' not in line:
            continue
        bdf = parts[0]
        if not _is_vf(bdf):
            continue
        try:
            physfn_path = os.path.realpath(f'/sys/bus/pci/devices/{bdf}/physfn')
            pf_bdf = os.path.basename(physfn_path)
            if pf_bdf in pf_set:
                vfs.append(bdf)
        except OSError:
            continue
    return sorted(vfs)


def detect_infiniband_devices() -> list[str]:
    """Detect InfiniBand devices for passthrough.

    Prefers SR-IOV VFs over PFs: if VFs exist for our PFs, returns VFs.
    Otherwise returns PFs (caller should create VFs via ensure_infiniband_vfs).
    """
    pfs = detect_infiniband_pfs()
    if not pfs:
        return []
    vfs = detect_infiniband_vfs(pfs)
    return vfs if vfs else pfs
