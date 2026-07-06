"""PCI device discovery for GPU, NVSwitch, and InfiniBand devices.

Most functions are self-contained: they read lspci / sysfs / nvidia-gpu-tools
output and return BDF lists or model mappings without modifying system state.
"""

import os
import glob
import platform
import re
import subprocess

from chutes.guest.gpu.profiles import GPU_PROFILES, GpuProfile, resolve_profile
from chutes.guest.gpu.tools import ensure_gpu_tools_available

_NVIDIA_VENDOR = '10de'
_MELLANOX_VENDOR = '15b3'


def detect_host_cpus() -> int | None:
    """Return the host's total logical CPU count from sysfs.

    Reads /sys/devices/system/cpu/present (e.g. '0-287') which reflects the
    CPUs present in hardware regardless of online/offline state. Returns None
    if the file is unreadable or unparseable.
    """
    try:
        present = open("/sys/devices/system/cpu/present").read().strip()
        parts = present.split(",")
        last = parts[-1]
        if "-" in last:
            return int(last.split("-")[1]) + 1
        return int(last) + 1
    except (OSError, ValueError):
        return None


def detect_host_sockets() -> int | None:
    """Return the number of physical CPU sockets from sysfs topology.

    Counts unique physical_package_id values across all CPU entries.
    Returns None if the topology files are unreadable.
    """
    packages: set[str] = set()
    for path in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology/physical_package_id"):
        try:
            packages.add(open(path).read().strip())
        except OSError:
            continue
    return len(packages) if packages else None


def detect_numa_node_count() -> int:
    """Return the number of NUMA nodes visible to the OS from sysfs."""
    return len(glob.glob("/sys/devices/system/node/node[0-9]*"))


def detect_host_mem_gb() -> int | None:
    """Return total host RAM in GB from /proc/meminfo MemTotal.

    Used to clamp guest RAM so a TDX VM (whose memory is pinned and
    unreclaimable) cannot be sized beyond what the host can physically back.
    Returns None if MemTotal is unreadable or unparseable.
    """
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return None
    return None


# Expected QEMU per Ubuntu release (each ships one build). Upstream version only;
# distro "+ds-...ubuntuX.Y" SRU revisions do not move RTMR0.
SUPPORTED_QEMU_BY_OS = {
    "25.10": "10.1.0",
    "26.04": "10.2.1",
}


def detect_os_version() -> str | None:
    """Return the host OS VERSION_ID (e.g. '26.04') from /etc/os-release, or None."""
    try:
        return platform.freedesktop_os_release().get("VERSION_ID")
    except (OSError, AttributeError):
        return None


def detect_qemu_version() -> str | None:
    """Return the host qemu-system-x86_64 upstream version (e.g. '10.2.1'), or None."""
    try:
        out = subprocess.run(
            ["qemu-system-x86_64", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
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
    expected = SUPPORTED_QEMU_BY_OS.get(os_version)
    if expected is None:
        raise ValueError(
            f"Host OS release {os_version!r} is not supported "
            f"{list(SUPPORTED_QEMU_BY_OS)}. Supported releases ship a QEMU whose "
            f"RTMR0 is baselined; run discover-profile.sh and send the output so "
            f"Chutes can baseline this release."
        )
    if qemu_version != expected:
        raise ValueError(
            f"Host OS {os_version} ships (and we baseline) QEMU {expected}, but "
            f"found QEMU {qemu_version}. A different QEMU generates different guest "
            f"ACPI tables → a different TDX RTMR0 → rejected at attestation. Update "
            f"to the release's QEMU (`sudo apt update && sudo apt full-upgrade`), or "
            f"run discover-profile.sh and send the output to baseline {qemu_version}."
        )


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


def _match_gpu_model(lspci_line: str, host_cpus: int | None = None) -> str | None:
    """Return the GPU_PROFILES key for an lspci line, or None.

    Uses PCI device ID as the primary discriminant. A profile must be an EXACT,
    unambiguous match: when multiple profiles share the same device ID (e.g. B200
    and B200_XEON6 both use 2901), host_cpus must select exactly one. If the
    topology cannot be resolved to a single supported profile — no profile matches
    the CPU count, or the CPU count is unavailable to disambiguate — a ValueError
    is raised rather than guessing. An unsupported/undetermined topology has no
    measurement baseline (MRTD/RTMR), so launching it with a guessed profile would
    produce a VM that cannot attest; the caller must surface it instead.
    """
    device_id = _extract_device_id(lspci_line, _NVIDIA_VENDOR)
    if not device_id:
        return None
    matches = [(name, profile) for name, profile in GPU_PROFILES.items()
               if profile.matches_device_id(device_id)]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0][0]

    # Multiple profiles share this device ID — require an exact CPU count match.
    known = {name: p.host_cpus for name, p in matches}
    if host_cpus is None:
        # No CPU count to disambiguate — refuse to guess. Returning the first match
        # could select a profile whose guest config (and therefore measurements)
        # does not correspond to this host.
        raise ValueError(
            f"Device ID {device_id} matches multiple profiles {known} but the host "
            f"CPU count could not be determined to disambiguate. Unsupported or "
            f"undetermined topology — refusing to guess a profile."
        )
    exact = [(name, p) for name, p in matches if p.host_cpus == host_cpus]
    if len(exact) == 1:
        return exact[0][0]
    raise ValueError(
        f"Device ID {device_id} matches multiple profiles {known} but none "
        f"has host_cpus={host_cpus}. Add a new profile for this CPU topology."
    )


def _is_known_gpu(lspci_line: str) -> bool:
    """True if the lspci line is an NVIDIA GPU whose device ID matches a known
    profile. Recognition only — does NOT resolve/disambiguate the exact profile,
    so it never raises on a shared device ID (unlike _match_gpu_model)."""
    device_id = _extract_device_id(lspci_line, _NVIDIA_VENDOR)
    if not device_id:
        return False
    return any(p.matches_device_id(device_id) for p in GPU_PROFILES.values())


def detect_nvidia_gpus() -> list[str]:
    """Detect NVIDIA GPU BDFs via lspci (vendor 10de, device IDs from GpuProfile.pci_device_ids)."""
    devices = []
    for line in _lspci_lines(_NVIDIA_VENDOR):
        parts = line.strip().split()
        if not parts:
            continue
        if _is_known_gpu(line):
            devices.append(parts[0])
    return sorted(devices)


def detect_gpu_numa_nodes(gpu_bdfs: list[str]) -> list[int]:
    """Return sorted list of unique NUMA node IDs that have GPUs attached.

    Reads /sys/bus/pci/devices/<bdf>/numa_node for each GPU BDF.
    Skips devices that report -1 (no NUMA affinity) or are unreadable.
    """
    nodes: set[int] = set()
    for bdf in gpu_bdfs:
        numa_path = f'/sys/bus/pci/devices/{bdf}/numa_node'
        try:
            with open(numa_path) as f:
                node = int(f.read().strip())
            if node >= 0:
                nodes.add(node)
        except (OSError, ValueError):
            continue
    return sorted(nodes)


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


def _device_numa_layout(bdfs: list[str]) -> tuple[int, ...]:
    """Per-device host NUMA node in sorted-BDF order (the guest PXB grouping that
    feeds RTMR0). Unknown/unreadable nodes recorded as -1."""
    layout: list[int] = []
    for bdf in sorted(bdfs):
        try:
            with open(f"/sys/bus/pci/devices/{bdf}/numa_node") as f:
                layout.append(int(f.read().strip()))
        except (OSError, ValueError):
            layout.append(-1)
    return tuple(layout)


def host_topology_fingerprint(
    profile: GpuProfile,
    gpu_bdfs: list[str],
    nvswitch_bdfs: list[str],
    ib_bdfs: list[str],
) -> tuple:
    """RTMR0-impacting topology fingerprint of passed-through devices (GPUs,
    NVSwitches, IB PFs). On the 2-node NUMA path, device->NUMA layout drives the
    guest PXB grouping; otherwise the guest is flat and only counts matter.
    ``ib_bdfs`` is empty for profiles that don't pass IB. Same fingerprint =>
    same RTMR0 for a given profile + QEMU + image."""
    node_count = detect_numa_node_count()
    if profile.enable_numa_topology and node_count == 2:
        return (
            "numa",
            _device_numa_layout(gpu_bdfs),
            _device_numa_layout(nvswitch_bdfs),
            _device_numa_layout(ib_bdfs),
        )
    return ("flat", len(gpu_bdfs), len(nvswitch_bdfs), len(ib_bdfs))


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
    """Map each GPU BDF to its GPU_PROFILES key (or 'default') via lspci.

    Host topology (CPU count) is detected automatically from sysfs to
    disambiguate profiles that share the same PCI device ID. Raises ValueError
    if the detected topology doesn't match any known profile for that device ID.
    """
    host_cpus = detect_host_cpus()
    bdf_set = set(bdfs)
    result = {}
    for line in _lspci_lines(_NVIDIA_VENDOR):
        parts = line.strip().split()
        if not parts:
            continue
        bdf = parts[0]
        if bdf not in bdf_set:
            continue
        result[bdf] = _match_gpu_model(line, host_cpus=host_cpus) or 'default'
    return result


# PCI class 0207 = InfiniBand controller. Excludes Ethernet [0200], DMA [0801], etc.
_PCI_CLASS_INFINIBAND = '0207'


def _is_vf(bdf: str) -> bool:
    """Return True if device is an SR-IOV Virtual Function (has physfn)."""
    physfn = f'/sys/bus/pci/devices/{bdf}/physfn'
    return os.path.exists(physfn)


def detect_cx7_bridge_pfs() -> list[str]:
    """Detect ConnectX-7 NVSwitch bridge Physical Function BDFs via VPD.

    On B200/B300 HGX systems the NVSwitch ASICs are not visible as PCIe devices.
    They are managed through ConnectX-7 bridge PFs whose Vital Product Data
    contains the field ``SMDL=SW_MNG``.  These PFs must stay on the host for
    Fabric Manager to communicate with the NVSwitches over InfiniBand; they
    must never be bound to vfio-pci or passed through to a guest.

    Regular ConnectX-7 NIC PFs share the same PCI device ID (15b3:1021) but
    their VPD does not contain ``SMDL=SW_MNG``, so this function correctly
    returns only the bridge PFs.

    Returns an empty list on non-Blackwell HGX hosts (VPD read is cheap; no NVSwitch
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


# ---------------------------------------------------------------------------
# Full topology detection and profile matching
# ---------------------------------------------------------------------------


def detect_profile() -> GpuProfile:
    """Probe host hardware topology and match to a registered GpuProfile.

    Collects GPU PCI device IDs, CPU count, socket count, NVSwitch presence,
    and InfiniBand PF presence, then verifies the detected topology matches a
    registered profile exactly. Raises ValueError on any mismatch — there is
    no partial match or advisory path.
    """
    gpu_bdfs = get_gpu_bdfs() or detect_nvidia_gpus()
    if not gpu_bdfs:
        raise ValueError("No GPU devices detected on this host.")

    gpu_models = get_gpu_models_from_lspci(gpu_bdfs)
    profile = resolve_profile(gpu_models)
    total_gpus = len(gpu_bdfs)

    detected_sockets = detect_host_sockets()
    if detected_sockets is not None and detected_sockets != profile.host_sockets:
        raise ValueError(
            f"Socket count mismatch: profile '{profile.name}' expects "
            f"{profile.host_sockets} socket(s), detected {detected_sockets}. "
            f"Verify lscpu and add a new profile if this is a different server SKU."
        )

    nvswitch_bdfs: list[str] = []
    if profile.should_passthrough_nvswitches(total_gpus):
        nvswitch_bdfs = detect_nvswitches()
        if not nvswitch_bdfs:
            raise ValueError(
                f"Profile '{profile.name}' requires NVSwitches for {total_gpus} GPU(s) "
                f"but none were detected. "
                f"Verify with: lspci -Dnn | grep '\\[0680\\]' | grep '10de'"
            )

    ib_bdfs: list[str] = []
    if profile.should_passthrough_infiniband:
        ib_bdfs = detect_infiniband_pfs(exclude_bdfs=detect_cx7_bridge_pfs())
        if not ib_bdfs:
            print(
                f"  Note: profile '{profile.name}' supports InfiniBand passthrough "
                f"but no IB devices detected on this host — skipping IB passthrough."
            )

    # Topology hard-match: the live topology must be one we've baselined for this
    # profile (it drives the guest ACPI and thus RTMR0). Empty set = not enforced.
    baselined = profile.baselined_topologies
    if baselined:
        fingerprint = host_topology_fingerprint(
            profile, gpu_bdfs, nvswitch_bdfs, ib_bdfs
        )
        if fingerprint not in baselined:
            raise ValueError(
                f"Host topology {fingerprint} is not baselined for profile "
                f"'{profile.name}'. Known: {sorted(baselined)}. This host would "
                f"attest with an unbaselined RTMR0 and be rejected. Run "
                f"discover-profile.sh and send the output to baseline it."
            )

    return profile
