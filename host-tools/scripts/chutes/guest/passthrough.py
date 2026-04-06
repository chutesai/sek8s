"""GPU passthrough for QEMU using per-SKU GpuProfile rules."""

import os
import subprocess

from chutes.guest.detection import (
    detect_infiniband_pfs,
    detect_infiniband_vfs,
    detect_nvidia_gpus,
    detect_nvswitches,
    get_gpu_bdfs,
    get_gpu_models_from_lspci,
)
from chutes.guest.gpu.profiles import GpuProfile, resolve_profile
from chutes.guest.gpu.tools import ensure_gpu_tools_available
from chutes.guest.qemu import PciTopologyState
from chutes.guest.vfio import (
    bind_explicit_devices_to_vfio,
    ensure_sriov_vfs,
    has_stale_vfio_devices,
    install_udev_rules,
    pci_cleanup_stale_devices,
)

_gpu_tools_cmd: str | None = None


def _run_gpu_tools(*args: str):
    """Run an nvidia-gpu-tools command with sudo.

    Resolves and caches the tool path on first call via ensure_gpu_tools_available().
    """
    global _gpu_tools_cmd
    if _gpu_tools_cmd is None:
        print('  Ensuring GPU admin tools are available...')
        _gpu_tools_cmd = ensure_gpu_tools_available()
    subprocess.check_call(
        ['sudo', _gpu_tools_cmd, *args],
        stderr=subprocess.STDOUT,
    )


def _scripts_dir() -> str:
    """Return the host-tools/scripts/ directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _configure_nvswitches(
    nvswitches: list[str],
    profile: GpuProfile,
    total_gpus: int,
):
    """Configure NVSwitches before VFIO binding (PPCIe mode only)."""
    if not (profile.should_passthrough_nvswitches(total_gpus) and nvswitches):
        return
    print('  Configuring NVSwitches for PPCIe mode...')
    for nvsw in nvswitches:
        print(f'  Preparing NVSwitch {nvsw} for PPCIe')
        _run_gpu_tools('--set-cc-mode=off', '--reset-after-cc-mode-switch', f'--gpu-bdf={nvsw}')
        _run_gpu_tools('--set-ppcie-mode=on', '--reset-after-ppcie-mode-switch', f'--gpu-bdf={nvsw}')


def _configure_gpus(
    gpus: list[str],
    profile: GpuProfile,
    total_gpus: int,
):
    """Configure each GPU's CC/PPCIe mode before VFIO binding."""
    print('  Configuring GPUs...')
    for gpu in gpus:
        mode_str = profile.describe_mode(total_gpus)
        print(f'  Preparing GPU {gpu} ({profile.name}) for {mode_str}')

        for tool_args in profile.get_cc_mode_args(total_gpus):
            _run_gpu_tools(*tool_args, f'--gpu-bdf={gpu}')


def _prepare_devices(
    gpus: list[str],
    nvswitches: list[str],
    ib_devices: list[str],
    profile: GpuProfile,
):
    """Clean stale PCI state, configure CC/PPCIe modes, bind to vfio-pci, udev."""
    total_gpus = len(gpus)

    all_devices = list(gpus)
    if profile.should_passthrough_nvswitches(total_gpus) and nvswitches:
        all_devices.extend(nvswitches)
    if ib_devices:
        all_devices.extend(ib_devices)

    if has_stale_vfio_devices(all_devices):
        print('  Stale vfio-pci devices detected from previous session')
        print('  SBR reset (via parent bridge) to restore device responsiveness...')
        _run_gpu_tools('--reset-with-sbr', '--reset-after-ppcie-mode-switch')
        print('  Cleaning stale PCI device state...')
        pci_cleanup_stale_devices(all_devices)

    _configure_nvswitches(nvswitches, profile, total_gpus)
    _configure_gpus(gpus, profile, total_gpus)

    print('  Binding devices to vfio-pci (explicit BDF list)...')
    bind_explicit_devices_to_vfio(all_devices)

    install_udev_rules(_scripts_dir())


def _build_pci_topology(
    cmd: list[str],
    gpus: list[str],
    nvswitches_for_vm: list[str],
    ib_devices: list[str],
    profile: GpuProfile,
):
    """Add GPU, NVSwitch, and IB devices to the QEMU PCI topology."""
    topo = PciTopologyState()

    print(f'  Adding {len(gpus)} GPU(s) to PCI topology...')
    for i, gpu in enumerate(gpus):
        bar_size = profile.bar_size_mb
        print(f'    GPU {gpu}: {profile.name} detected, using {bar_size} MB BAR')
        topo.add_device(
            cmd,
            host_bdf=gpu,
            rp_id=f'rp{i + 1}',
            chassis=i + 1,
            bar_size_mb=bar_size,
            bar_index=i + 1,
        )

    if nvswitches_for_vm:
        print(f'  Adding {len(nvswitches_for_vm)} NVSwitch(es) to PCI topology...')
    for j, nvsw in enumerate(nvswitches_for_vm):
        topo.add_device(
            cmd,
            host_bdf=nvsw,
            rp_id=f'rp_nvsw{j + 1}',
            chassis=len(gpus) + j + 1,
        )

    if ib_devices:
        print(f'  Adding {len(ib_devices)} InfiniBand device(s) to PCI topology...')
    for k, ib_dev in enumerate(ib_devices):
        topo.add_device(
            cmd,
            host_bdf=ib_dev,
            rp_id=f'rp_ib{k + 1}',
            chassis=len(gpus) + len(nvswitches_for_vm) + k + 1,
        )

    print(
        f'  Passthrough configured: {len(gpus)} GPU(s), '
        f'{len(nvswitches_for_vm)} NVSwitch(es), '
        f'{len(ib_devices)} IB device(s)'
    )


def setup_passthrough(cmd: list[str]):
    """Detect passthrough devices, prepare and bind them on the host, extend cmd for QEMU."""
    gpus = get_gpu_bdfs()
    if not gpus:
        gpus = detect_nvidia_gpus()
    if not gpus:
        return

    gpu_models = get_gpu_models_from_lspci(gpus)
    profile = resolve_profile(gpu_models)
    total_gpus = len(gpus)

    nvswitches = (
        detect_nvswitches()
        if profile.should_passthrough_nvswitches(total_gpus)
        else []
    )

    ib_devices: list[str] = []
    if profile.should_passthrough_infiniband:
        ib_pfs = detect_infiniband_pfs()
        if ib_pfs:
            print(f'  Creating SR-IOV VFs from {len(ib_pfs)} InfiniBand PF(s)...')
            for pf in ib_pfs:
                if ensure_sriov_vfs(pf):
                    print(f'    {pf} → VF(s) created')
                else:
                    print(f'    Warning: Could not create VFs on {pf}')
            ib_devices = detect_infiniband_vfs(ib_pfs)
            if not ib_devices:
                print('  Warning: No InfiniBand VFs found after creation')

    print(f'  Detected {len(gpus)} GPUs: {gpus}')
    if nvswitches:
        print(f'  Detected {len(nvswitches)} NVSwitches: {nvswitches}')
    if ib_devices:
        print(f'  Detected {len(ib_devices)} InfiniBand device(s): {ib_devices}')
    print(f'  Mode: {profile.describe_mode(total_gpus)}')

    _prepare_devices(gpus, nvswitches, ib_devices, profile)
    cmd.extend(['-object', 'iommufd,id=iommufd0'])

    nvswitches_for_vm = (
        nvswitches
        if profile.should_passthrough_nvswitches(total_gpus)
        else []
    )

    _build_pci_topology(cmd, gpus, nvswitches_for_vm, ib_devices, profile)
