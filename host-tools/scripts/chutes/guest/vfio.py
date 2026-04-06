"""VFIO device binding, PCI cleanup, SR-IOV VF creation, and udev rules."""

import os
import subprocess
import time

# Number of SR-IOV VFs to create per InfiniBand PF for VM passthrough
IB_VFS_PER_PF = 1


def ensure_sriov_vfs(pf_bdf: str, num_vfs: int = IB_VFS_PER_PF) -> bool:
    """Create SR-IOV VFs on a Physical Function. Returns True if successful.

    Writes to /sys/bus/pci/devices/<pf>/sriov_numvfs. PF stays bound to mlx5_core.
    """
    sriov_path = f'/sys/bus/pci/devices/{pf_bdf}/sriov_numvfs'
    if not os.path.exists(sriov_path):
        return False
    try:
        with open(sriov_path, 'r') as f:
            current = int(f.read().strip())
        if current >= num_vfs:
            return True
        if current > 0:
            with open(sriov_path, 'w') as f:
                f.write('0')
    except (OSError, ValueError):
        return False
    try:
        with open(sriov_path, 'w') as f:
            f.write(str(num_vfs))
        return True
    except OSError:
        return False


def load_vfio_modules():
    """Load VFIO kernel modules required for PCI passthrough."""
    modules = ['vfio_pci', 'vfio_iommu_type1', 'vfio_virqfd']
    for module in modules:
        try:
            subprocess.run(['modprobe', module], check=False, capture_output=True)
        except Exception:
            pass


def bind_device_to_vfio(device_bdf: str):
    """Bind a single device to vfio-pci using driver_override method.

    If the device is already bound (e.g. mlx5_core for Mellanox IB), we must
    unbind it first; driver_override + probe alone may not take over.
    """
    driver_override_path = f'/sys/bus/pci/devices/{device_bdf}/driver_override'
    driver_link = f'/sys/bus/pci/devices/{device_bdf}/driver'
    try:
        with open(driver_override_path, 'w') as f:
            f.write('vfio-pci')
        # Unbind from current driver if bound (e.g. mlx5_core for Mellanox IB)
        if os.path.islink(driver_link):
            driver_name = os.path.basename(os.path.realpath(driver_link))
            if driver_name != 'vfio-pci':
                unbind_path = f'/sys/bus/pci/drivers/{driver_name}/unbind'
                if os.path.exists(unbind_path):
                    with open(unbind_path, 'w') as f:
                        f.write(device_bdf)
        with open('/sys/bus/pci/drivers_probe', 'w') as f:
            f.write(device_bdf)
    except Exception as e:
        print(f'  Warning: Failed to bind {device_bdf} to vfio-pci: {e}')


def bind_explicit_devices_to_vfio(devices: list[str]):
    """Bind only the given BDFs to vfio-pci (no IOMMU group binding).

    Matches setup-gpus.sh semantics: explicit device list only, no bridges
    or unrelated fabric endpoints.
    """
    load_vfio_modules()
    for device in devices:
        bind_device_to_vfio(device)
        print(f'    {device} → vfio-pci')


def _get_bound_driver(device_bdf: str) -> str | None:
    """Return the driver name currently bound to a PCI device, or None."""
    driver_link = f'/sys/bus/pci/devices/{device_bdf}/driver'
    if os.path.islink(driver_link):
        return os.path.basename(os.path.realpath(driver_link))
    return None


def _sysfs_write(path: str, value: str, timeout: float = 10.0) -> bool:
    """Write to a sysfs file via subprocess with a timeout.

    Direct Python file writes to sysfs can block in uninterruptible kernel
    sleep when a PCI device's config space is inaccessible.  Spawning a
    subprocess lets us enforce a timeout and continue even if the kernel
    operation hangs.
    """
    try:
        subprocess.run(
            ['sudo', 'bash', '-c', f'echo {value} > {path}'],
            timeout=timeout,
            capture_output=True,
        )
        return True
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return False


def has_stale_vfio_devices(devices: list[str]) -> bool:
    """Return True if any device in the list is currently bound to vfio-pci."""
    return any(_get_bound_driver(bdf) == 'vfio-pci' for bdf in devices)


def pci_cleanup_stale_devices(
    devices: list[str],
    timeout: float = 5.0,
    per_device_timeout: float = 10.0,
):
    """Remove PCI devices with stale vfio-pci bindings and rescan.

    After a QEMU session exits, devices remain bound to vfio-pci with stale
    iommufd references.  Removing and rescanning clears all kernel-level state
    (driver bindings, iommufd refs, AER errors, cached config space) so the
    next QEMU launch starts clean.

    An SBR reset should be performed before calling this function — the PCI
    remove path accesses device config space, which hangs if the device is
    unresponsive.  SBR goes through the parent bridge and makes devices
    accessible again.

    On first boot (no previous QEMU session), devices won't be on vfio-pci
    and this function is a no-op.
    """
    removed: list[str] = []
    for bdf in devices:
        if _get_bound_driver(bdf) != 'vfio-pci':
            continue
        remove_path = f'/sys/bus/pci/devices/{bdf}/remove'
        print(f'    Removing {bdf} (was vfio-pci)...', flush=True)
        if _sysfs_write(remove_path, '1', timeout=per_device_timeout):
            removed.append(bdf)
            print(f'    {bdf} removed')
        else:
            print(f'    Warning: {bdf} remove timed out (device may be unresponsive)')

    if not removed:
        return

    print('  Rescanning PCI bus...', flush=True)
    if not _sysfs_write('/sys/bus/pci/rescan', '1', timeout=per_device_timeout):
        print('  Warning: PCI rescan timed out')
        return

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        missing = [
            bdf for bdf in removed
            if not os.path.exists(f'/sys/bus/pci/devices/{bdf}')
        ]
        if not missing:
            print(f'  All {len(removed)} device(s) reappeared after rescan')
            return
        time.sleep(0.5)

    still_missing = [
        bdf for bdf in removed
        if not os.path.exists(f'/sys/bus/pci/devices/{bdf}')
    ]
    if still_missing:
        print(f'  Warning: devices did not reappear after rescan: {still_missing}')


def install_udev_rules(scripts_dir: str):
    """Install vfio-passthrough udev rules if not already present."""
    udev_rules_src = os.path.join(scripts_dir, 'devices', 'vfio-passthrough.rules')
    udev_rules_dst = '/etc/udev/rules.d/vfio-passthrough.rules'
    if not os.path.exists(udev_rules_src):
        raise FileNotFoundError(
            f"Udev rules file not found: {udev_rules_src}. "
            "This file should be in the scripts directory."
        )
    if not os.path.exists(udev_rules_dst):
        print('  Installing udev rules...')
        subprocess.check_call(
            ['sudo', 'cp', udev_rules_src, '/etc/udev/rules.d/'],
            stderr=subprocess.STDOUT,
        )
        subprocess.check_call(
            ['sudo', 'udevadm', 'control', '--reload-rules'],
            stderr=subprocess.STDOUT,
        )
        subprocess.check_call(
            ['sudo', 'udevadm', 'trigger'],
            stderr=subprocess.STDOUT,
        )
    else:
        print('  Udev rules already present (skipping install)')
