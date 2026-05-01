"""GPU passthrough for QEMU using per-SKU GpuProfile rules."""

import os
import subprocess
import time

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
    unbind_stale_vfio_devices,
)

_gpu_tools_cmd: str | None = None


GPU_TOOLS_TIMEOUT_SECS = 120


def _run_gpu_tools(*args: str):
    """Run an nvidia-gpu-tools command with sudo.

    Resolves and caches the tool path on first call via ensure_gpu_tools_available().
    Times out after GPU_TOOLS_TIMEOUT_SECS to prevent indefinite hangs when GPU
    hardware is wedged at the PCIe level.
    """
    global _gpu_tools_cmd
    if _gpu_tools_cmd is None:
        print('  Ensuring GPU admin tools are available...')
        _gpu_tools_cmd = ensure_gpu_tools_available()
    cmd = ['sudo', _gpu_tools_cmd, *args]
    try:
        subprocess.run(
            cmd,
            check=True,
            stderr=subprocess.STDOUT,
            timeout=GPU_TOOLS_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f'nvidia-gpu-tools timed out after {GPU_TOOLS_TIMEOUT_SECS}s '
            f'(args: {args}). GPU hardware may be wedged — a host reboot is '
            f'likely required to recover PCIe state.'
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


def _device_config_readable(bdf: str) -> bool:
    """Return True if the device's PCI config space responds (vendor ID read)."""
    vendor_path = f'/sys/bus/pci/devices/{bdf}/vendor'
    try:
        result = subprocess.run(
            ['cat', vendor_path],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() != b'0xffff'
    except (subprocess.TimeoutExpired, OSError):
        return False


def _wait_devices_ready(devices: list[str], timeout_secs: int = 30) -> bool:
    """Poll until all devices respond to config-space reads, or timeout."""
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        if all(_device_config_readable(bdf) for bdf in devices):
            return True
        time.sleep(2)
    unready = [bdf for bdf in devices if not _device_config_readable(bdf)]
    if unready:
        print(f'  Warning: devices still unresponsive after {timeout_secs}s: {unready}')
    return not unready


SBR_SETTLE_SECS = 30


def _prepare_devices(
    gpus: list[str],
    nvswitches: list[str],
    ib_devices: list[str],
    profile: GpuProfile,
):
    """Clean stale PCI state, configure CC/PPCIe modes, bind to vfio-pci, udev.

    Strategy: try lightweight unbind first (works after clean VM shutdown).
    Only escalate to SBR if unbind fails (device truly wedged from crash).
    """
    total_gpus = len(gpus)

    all_devices = list(gpus)
    if profile.should_passthrough_nvswitches(total_gpus) and nvswitches:
        all_devices.extend(nvswitches)
    if ib_devices:
        all_devices.extend(ib_devices)

    if has_stale_vfio_devices(all_devices):
        print('  Stale vfio-pci devices detected from previous session')
        print('  Unbinding stale vfio-pci devices (no SBR needed for clean shutdown)...')
        unbind_stale_vfio_devices(all_devices)

        if has_stale_vfio_devices(all_devices):
            print('  Some devices could not be unbound — escalating to SBR reset...')
            _run_gpu_tools('--reset-with-sbr', '--reset-after-ppcie-mode-switch')
            print(f'  Waiting {SBR_SETTLE_SECS}s for devices to re-initialize after SBR...')
            time.sleep(SBR_SETTLE_SECS)
            print('  Verifying device responsiveness...')
            if not _wait_devices_ready(all_devices):
                raise RuntimeError(
                    'Devices unresponsive after SBR reset. '
                    'A host reboot is likely required.'
                )
            print('  Retrying unbind after SBR...')
            unbind_stale_vfio_devices(all_devices)

    if not _wait_devices_ready(all_devices, timeout_secs=10):
        raise RuntimeError(
            'GPU/NVSwitch devices not responding to config-space reads. '
            'Cannot proceed with CC/PPCIe mode configuration. '
            'A host reboot may be required.'
        )

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
