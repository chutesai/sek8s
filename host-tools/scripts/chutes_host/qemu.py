"""QEMU command construction for TDX VM launch.

Builds the full qemu-system-x86_64 command line including base TDX
configuration, PCI device topology, networking, volumes, and vsock.
"""

import sys


def _block_format(path: str | None) -> str:
    """Infer block format from path. Returns 'raw' or 'qcow2'. Defaults to raw."""
    if not path:
        return "raw"
    if path.lower().endswith(".qcow2"):
        return "qcow2"
    return "raw"


class PciTopologyState:
    """Tracks PCIe root port allocation across GPUs, NVSwitches, and IB devices."""

    def __init__(self, start_port: int = 16, start_slot: int = 0x5):
        self.port = start_port
        self.slot = start_slot
        self.func = 0

    def add_device(
        self,
        cmd: list[str],
        host_bdf: str,
        rp_id: str,
        chassis: int,
        *,
        bar_size_mb: int | None = None,
        bar_index: int | None = None,
    ):
        """Add a vfio-pci device on a new PCIe root port.

        Args:
            cmd: QEMU command list to extend.
            host_bdf: PCI BDF of the host device.
            rp_id: Root port identifier (e.g. 'rp1', 'rp_nvsw1').
            chassis: Chassis number for the root port.
            bar_size_mb: Optional MMIO BAR size hint (fw_cfg).
            bar_index: 1-based fw_cfg index (only needed when bar_size_mb is set).
        """
        if self.func == 0:
            cmd.extend([
                '-device',
                f'pcie-root-port,port={self.port},chassis={chassis},id={rp_id},'
                f'bus=pcie.0,multifunction=on,addr={self.slot:#x}',
            ])
        else:
            cmd.extend([
                '-device',
                f'pcie-root-port,port={self.port},chassis={chassis},id={rp_id},'
                f'bus=pcie.0,addr={self.slot:#x}.{self.func:#x}',
            ])

        cmd.extend([
            '-device',
            f'vfio-pci,host={host_bdf},bus={rp_id},addr=0x0,iommufd=iommufd0',
        ])

        if bar_size_mb is not None and bar_index is not None:
            cmd.extend([
                '-fw_cfg',
                f'name=opt/ovmf/X-PciMmio64Mb{bar_index},string={bar_size_mb}',
            ])

        self.port += 1
        self.func = (self.func + 1) % 8
        if self.func == 0:
            self.slot += 1


def build_base_cmd(
    *,
    mem: str,
    vcpus: str,
    process_name: str,
    cpu_args: str,
    firmware: str,
    img_path: str,
    foreground: bool,
    pidfile: str,
    logfile: str,
) -> list[str]:
    """Build the base QEMU command (TDX, firmware, CPU, memory, boot disk)."""
    cmd = [
        'qemu-system-x86_64',
        '-accel', 'kvm',
        '-m', mem,
        '-smp', vcpus,
        '-name', f'{process_name},process={process_name},debug-threads=on',
        '-cpu', cpu_args,
        '-object', '{"qom-type":"tdx-guest","id":"tdx","quote-generation-socket":{"type":"vsock","cid":"2","port":"4050"}}',
        '-object', f'memory-backend-ram,id=mem0,size={mem},prealloc=yes',
        '-machine', 'q35,kernel_irqchip=split,confidential-guest-support=tdx,memory-backend=mem0',
        '-bios', firmware,
        '-nodefaults',
        '-vga', 'none',
    ]

    if foreground:
        cmd.extend(['-nographic', '-serial', 'mon:stdio'])
    else:
        cmd.extend([
            '-nographic',
            '-serial', f'file:{logfile}',
            '-daemonize',
            '-pidfile', pidfile,
        ])

    img_fmt = _block_format(img_path)
    drive_opts = f'file={img_path},if=none,id=virtio-disk0,cache=none,aio=native,format={img_fmt}'
    if img_fmt == "raw":
        drive_opts += ",discard=on,detect-zeroes=on"
    cmd.extend(["-drive", drive_opts])
    dev_opts = "virtio-blk-pci,drive=virtio-disk0"
    if img_fmt == "raw":
        dev_opts += ",num-queues=4"
    cmd.extend(["-device", dev_opts])

    return cmd


def build_network(cmd: list[str], *, network_type: str, net_iface: str | None, ssh_port: int):
    """Add networking configuration to QEMU command."""
    if network_type == "tap":
        if not net_iface:
            print("ERROR: --network-type tap requires --net-iface")
            sys.exit(1)
        print(f"Networking: TAP mode (iface={net_iface})")
        cmd.extend([
            '-netdev', f'tap,id=n0,ifname={net_iface},script=no,downscript=no',
            '-device', 'virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56',
        ])
    else:
        print("Networking: Canonical user-mode networking")
        cmd.extend([
            '-device', 'virtio-net-pci,netdev=nic0_td',
            '-netdev', f'user,id=nic0_td,hostfwd=tcp::{ssh_port}-:22',
        ])


def add_volumes(
    cmd: list[str],
    *,
    config_volume: str | None,
    cache_volume: str | None,
    storage_volume: str | None,
):
    """Add config, cache, and storage volumes to QEMU command."""
    if config_volume:
        cmd.extend([
            "-drive",
            f"file={config_volume},if=virtio,format=qcow2,readonly=on,cache=none",
        ])
    for vol_path, vol_id in [(cache_volume, "virtio-cache"), (storage_volume, "virtio-storage")]:
        if not vol_path:
            continue
        vol_fmt = _block_format(vol_path)
        drive_opts = f"file={vol_path},if=none,id={vol_id},cache=none,aio=native,format={vol_fmt}"
        if vol_fmt == "raw":
            drive_opts += ",discard=on,detect-zeroes=on"
        cmd.extend(["-drive", drive_opts])
        dev_opts = f"virtio-blk-pci,drive={vol_id}"
        if vol_fmt == "raw":
            dev_opts += ",num-queues=4"
        cmd.extend(["-device", dev_opts])


def add_vsock(cmd: list[str]):
    """Add vhost-vsock device to QEMU command."""
    cmd.extend(['-device', 'vhost-vsock-pci,guest-cid=3'])
