"""Profile-driven GPU passthrough orchestration.

Replaces the old add_gpu_passthrough() + prepare_gpus() with a single
entry point that uses GpuProfile to drive all type-specific decisions.
"""

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
    install_udev_rules,
    virsh_bind_device,
)


def _scripts_dir() -> str:
    """Return the host-tools/scripts/ directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _configure_nvswitches(
    nvswitches: list[str],
    profile: GpuProfile,
    total_gpus: int,
    cmd_base: list[str],
):
    """Configure NVSwitches before VFIO binding (PPCIe mode only)."""
    if not (profile.should_passthrough_nvswitches(total_gpus) and nvswitches):
        return
    print('  Configuring NVSwitches for PPCIe mode...')
    for nvsw in nvswitches:
        print(f'  Preparing NVSwitch {nvsw} for PPCIe')
        subprocess.check_call(
            cmd_base + ['--set-cc-mode=off', '--reset-after-cc-mode-switch', f'--gpu-bdf={nvsw}'],
            stderr=subprocess.STDOUT,
        )
        subprocess.check_call(
            cmd_base + ['--set-ppcie-mode=on', '--reset-after-ppcie-mode-switch', f'--gpu-bdf={nvsw}'],
            stderr=subprocess.STDOUT,
        )


def _configure_gpus(
    gpus: list[str],
    profile: GpuProfile,
    total_gpus: int,
    cmd_base: list[str],
):
    """Configure each GPU's CC/PPCIe mode before VFIO binding."""
    print('  Configuring GPUs...')
    for gpu in gpus:
        mode_str = profile.describe_mode(total_gpus)
        print(f'  Preparing GPU {gpu} ({profile.name}) for {mode_str}')

        for tool_args in profile.get_cc_mode_args(total_gpus):
            subprocess.check_call(
                cmd_base + tool_args + [f'--gpu-bdf={gpu}'],
                stderr=subprocess.STDOUT,
            )


def _prepare_devices(
    gpus: list[str],
    nvswitches: list[str],
    ib_devices: list[str],
    profile: GpuProfile,
):
    """Configure modes, bind to VFIO, and install udev rules.

    Order: configure GPU modes with nvidia-gpu-tools BEFORE binding to vfio-pci.
    Binding first would prevent the tools from accessing the devices.
    """
    print('  Ensuring GPU admin tools are available...')
    nvidia_tools_cmd = ensure_gpu_tools_available()
    cmd_base = ['sudo', nvidia_tools_cmd]
    total_gpus = len(gpus)

    _configure_nvswitches(nvswitches, profile, total_gpus, cmd_base)
    _configure_gpus(gpus, profile, total_gpus, cmd_base)

    devices_to_bind = list(gpus)
    if profile.should_passthrough_nvswitches(total_gpus) and nvswitches:
        devices_to_bind.extend(nvswitches)
    if ib_devices:
        devices_to_bind.extend(ib_devices)

    print('  Binding devices to vfio-pci (explicit BDF list)...')
    bind_explicit_devices_to_vfio(devices_to_bind)

    for gpu in gpus:
        virsh_bind_device(gpu)
    for ib_dev in ib_devices:
        virsh_bind_device(ib_dev)

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
    """Discover, prepare, and add all passthrough devices to the QEMU command.

    This is the single entry point called by __main__.launch_vm().
    """
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
