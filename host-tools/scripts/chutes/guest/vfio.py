"""VFIO device binding, virsh helpers, SR-IOV VF creation, and udev rule installation."""

import os
import subprocess

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


def virsh_bind_device(device_bdf: str):
    """Reattach then detach a PCI device via virsh (matches setup-gpus.sh behavior)."""
    virsh_bdf = device_bdf.replace(':', '_').replace('.', '_')
    print(f'  Binding {device_bdf} to vfio-pci via virsh')
    subprocess.check_call(
        ['sudo', 'virsh', 'nodedev-reattach', f'pci_{virsh_bdf}'],
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ['sudo', 'virsh', 'nodedev-detach', f'pci_{virsh_bdf}'],
        stderr=subprocess.STDOUT,
    )


def install_udev_rules(scripts_dir: str):
    """Install vfio-passthrough udev rules if not already present."""
    udev_rules_src = os.path.join(scripts_dir, 'vfio-passthrough.rules')
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
