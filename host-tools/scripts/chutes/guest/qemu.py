"""QEMU command construction for TDX VM launch.

Builds the full qemu-system-x86_64 command line including base TDX
configuration, PCI device topology, networking, volumes, and vsock.
"""

import os
import re
import sys


def _block_format(path: str | None) -> str:
    """Infer block format from path. Returns 'raw' or 'qcow2'. Defaults to raw."""
    if not path:
        return "raw"
    if path.lower().endswith(".qcow2"):
        return "qcow2"
    return "raw"


def _parse_mem_mib(mem: str) -> int:
    """Parse QEMU memory size string (e.g. '1536G', '512M') to MiB."""
    match = re.match(r"^(\d+)([GgMm])$", mem)
    if not match:
        raise ValueError(f"Invalid memory size: {mem!r}")
    value = int(match.group(1))
    unit = match.group(2).upper()
    if unit == "G":
        return value * 1024
    return value


def host_numa_nodes() -> list[int]:
    """Return sorted host NUMA node IDs from sysfs."""
    node_dir = "/sys/devices/system/node"
    nodes: list[int] = []
    try:
        for name in os.listdir(node_dir):
            if name.startswith("node") and name[4:].isdigit():
                nodes.append(int(name[4:]))
    except OSError:
        return []
    return sorted(nodes)


def use_numa_topology(enable_numa_topology: bool) -> bool:
    """True when profile requests NUMA and host has exactly 2 NUMA nodes."""
    return enable_numa_topology and len(host_numa_nodes()) == 2


class PcieRootPinning:
    """Pin emulated virtio devices to pcie.0 below PXB bridge slots (0x18+)."""

    _SLOTS = (0x2, 0x3, 0x4, 0x5, 0x6, 0x7)

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._index = 0

    def device_suffix(self) -> str:
        if not self.enabled:
            return ""
        if self._index >= len(self._SLOTS):
            raise RuntimeError("No free pcie.0 slots for emulated PCI devices")
        addr = self._SLOTS[self._index]
        self._index += 1
        return f",bus=pcie.0,addr=0x{addr:x}"


def read_pci_numa_node(bdf: str) -> int:
    """Read PCI device NUMA node from sysfs. Returns -1 if unknown."""
    path = f"/sys/bus/pci/devices/{bdf}/numa_node"
    try:
        with open(path) as f:
            node = int(f.read().strip())
    except (OSError, ValueError):
        return -1
    return node if node >= 0 else -1


def _append_numa_memory(cmd: list[str], mem_mib: int, host_nodes: list[int]) -> None:
    """Add per-node memory backends and guest NUMA topology."""
    num_nodes = len(host_nodes)
    per_node_mib = mem_mib // num_nodes
    for i, hnode in enumerate(host_nodes):
        if i == num_nodes - 1:
            node_size_mib = mem_mib - per_node_mib * (num_nodes - 1)
        else:
            node_size_mib = per_node_mib
        cmd.extend([
            "-object",
            (
                f"memory-backend-ram,id=mem-node{i},size={node_size_mib}M,"
                f"prealloc=on,prealloc-threads=32,host-nodes={hnode},policy=bind"
            ),
        ])
        cmd.extend(["-numa", f"node,nodeid={i},memdev=mem-node{i}"])
        cmd.extend(["-numa", f"cpu,node-id={i},socket-id={i}"])

    if num_nodes == 2:
        cmd.extend(["-numa", "dist,src=0,dst=1,val=21"])


class PciTopologyState:
    """Tracks PCIe root port allocation across GPUs, NVSwitches, and IB devices."""

    def __init__(self, start_port: int = 16, start_slot: int = 0x8):
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
            bar_size_mb: Optional MMIO BAR size hint (fw_cfg opt/ovmf/X-PciMmio64Mb).
            bar_index: 1-based fw_cfg index (only when bar_size_mb is set).
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


class NumaPciTopologyState:
    """PXB-PCIe grouped topology: one expander bridge per host NUMA node."""

    def __init__(self, start_port: int = 16):
        self.port = start_port
        self.pxb_created: dict[int, str] = {}
        self.pxb_port_idx: dict[int, int] = {}
        self.pxb_busnr = 128
        self._flat = PciTopologyState(start_port=start_port)

    def _ensure_pxb(self, cmd: list[str], numa_node: int) -> str:
        if numa_node not in self.pxb_created:
            pxb_id = f"pxb_numa{numa_node}"
            pxb_addr = f"0x{24 + numa_node:x}"
            cmd.extend([
                "-device",
                (
                    f"pxb-pcie,bus_nr={self.pxb_busnr},id={pxb_id},"
                    f"numa_node={numa_node},bus=pcie.0,addr={pxb_addr}"
                ),
            ])
            self.pxb_created[numa_node] = pxb_id
            self.pxb_port_idx[numa_node] = 0
            self.pxb_busnr += 32
            print(f"    Created PXB-PCIe for NUMA node {numa_node} (bus_nr={self.pxb_busnr - 32})")
        return self.pxb_created[numa_node]

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
        """Add a vfio-pci device under the PXB for its host NUMA node."""
        numa_node = read_pci_numa_node(host_bdf)
        if numa_node < 0:
            self._flat.add_device(
                cmd,
                host_bdf,
                rp_id,
                chassis,
                bar_size_mb=bar_size_mb,
                bar_index=bar_index,
            )
            return

        pxb_bus = self._ensure_pxb(cmd, numa_node)
        port_idx = self.pxb_port_idx[numa_node]
        rp_addr = f"0x{port_idx + 1:x}"
        self.pxb_port_idx[numa_node] = port_idx + 1
        cmd.extend([
            "-device",
            f"pcie-root-port,port={self.port},chassis={chassis},id={rp_id},bus={pxb_bus},addr={rp_addr}",
            "-device",
            f"vfio-pci,host={host_bdf},bus={rp_id},addr=0x0,iommufd=iommufd0",
        ])
        if bar_size_mb is not None and bar_index is not None:
            cmd.extend([
                "-fw_cfg",
                f"name=opt/ovmf/X-PciMmio64Mb{bar_index},string={bar_size_mb}",
            ])
        print(f"    {host_bdf} -> PXB NUMA node {numa_node}")
        self.port += 1


def build_base_cmd(
    *,
    mem: str,
    smp_topology: str,
    process_name: str,
    cpu_args: str,
    firmware: str,
    img_path: str,
    foreground: bool,
    pidfile: str,
    logfile: str,
    enable_numa_topology: bool = False,
    pci_pinning: PcieRootPinning | None = None,
) -> list[str]:
    """Build the base QEMU command (TDX, firmware, CPU, memory, boot disk)."""
    numa_enabled = use_numa_topology(enable_numa_topology)
    pinning = pci_pinning or PcieRootPinning(numa_enabled)
    host_nodes = host_numa_nodes() if numa_enabled else []

    cmd = [
        'qemu-system-x86_64',
        '-accel', 'kvm',
        '-m', mem,
        '-smp', smp_topology,
        '-name', f'{process_name},process={process_name},debug-threads=on',
        '-cpu', cpu_args,
        '-object', '{"qom-type":"tdx-guest","id":"tdx","quote-generation-socket":{"type":"vsock","cid":"2","port":"4050"}}',
    ]

    if numa_enabled:
        mem_mib = _parse_mem_mib(mem)
        _append_numa_memory(cmd, mem_mib, host_nodes)
        cmd.extend(['-machine', 'q35,kernel_irqchip=split,confidential-guest-support=tdx'])
        print(
            f"NUMA: {len(host_nodes)} guest nodes, "
            f"{mem_mib // len(host_nodes)}M each (approx), host nodes {host_nodes}"
        )
    else:
        cmd.extend([
            '-object', f'memory-backend-ram,id=mem0,size={mem}',
            '-machine', 'q35,kernel_irqchip=split,confidential-guest-support=tdx,memory-backend=mem0',
        ])

    cmd.extend([
        '-bios', firmware,
        '-nodefaults',
        '-vga', 'none',
    ])

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
    if img_fmt == "qcow2":
        drive_opts += ",discard=unmap"
    elif img_fmt == "raw":
        drive_opts += ",discard=on,detect-zeroes=on"
    cmd.extend(["-drive", drive_opts])
    dev_opts = f"virtio-blk-pci,drive=virtio-disk0,bootindex=0{pinning.device_suffix()}"
    if img_fmt == "raw":
        dev_opts += ",num-queues=4"
    cmd.extend(["-device", dev_opts])

    return cmd


def build_network(
    cmd: list[str],
    *,
    network_type: str,
    net_iface: str | None,
    ssh_port: int,
    net_queues: int = 4,
    pci_pinning: PcieRootPinning | None = None,
):
    """Add networking configuration to QEMU command."""
    pinning = pci_pinning or PcieRootPinning(False)
    if network_type == "tap":
        if not net_iface:
            print("ERROR: --network-type tap requires --net-iface")
            sys.exit(1)
        vectors = 2 * net_queues + 2
        print(f"Networking: TAP mode (iface={net_iface}, queues={net_queues}, vhost=on)")
        cmd.extend([
            '-netdev',
            f'tap,id=n0,ifname={net_iface},script=no,downscript=no,vhost=on,queues={net_queues}',
            '-device',
            f'virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56,mq=on,vectors={vectors},mrg_rxbuf=on'
            f'{pinning.device_suffix()}',
        ])
    else:
        print("Networking: Canonical user-mode networking")
        cmd.extend([
            '-device',
            f'virtio-net-pci,netdev=nic0_td{pinning.device_suffix()}',
            '-netdev', f'user,id=nic0_td,hostfwd=tcp::{ssh_port}-:22',
        ])


def add_volumes(
    cmd: list[str],
    *,
    config_volume: str | None,
    cache_volume: str | None,
    storage_volume: str | None,
    pci_pinning: PcieRootPinning | None = None,
):
    """Add config, cache, and storage volumes to QEMU command."""
    pinning = pci_pinning or PcieRootPinning(False)
    if config_volume:
        cmd.extend([
            "-drive",
            f"file={config_volume},if=none,id=virtio-config,cache=none,format=qcow2,readonly=on",
        ])
        cmd.extend([
            "-device",
            f"virtio-blk-pci,drive=virtio-config{pinning.device_suffix()}",
        ])
    for vol_path, vol_id in [(cache_volume, "virtio-cache"), (storage_volume, "virtio-storage")]:
        if not vol_path:
            continue
        vol_fmt = _block_format(vol_path)
        drive_opts = f"file={vol_path},if=none,id={vol_id},cache=none,aio=native,format={vol_fmt}"
        if vol_fmt == "raw":
            drive_opts += ",discard=on,detect-zeroes=on"
        cmd.extend(["-drive", drive_opts])
        dev_opts = f"virtio-blk-pci,drive={vol_id}{pinning.device_suffix()}"
        if vol_fmt == "raw":
            dev_opts += ",num-queues=4"
        cmd.extend(["-device", dev_opts])


def add_vsock(cmd: list[str], *, pci_pinning: PcieRootPinning | None = None):
    """Add vhost-vsock device to QEMU command."""
    pinning = pci_pinning or PcieRootPinning(False)
    cmd.extend([f'-device', f'vhost-vsock-pci,guest-cid=3{pinning.device_suffix()}'])
