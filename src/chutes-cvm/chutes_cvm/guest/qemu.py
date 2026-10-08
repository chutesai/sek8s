"""QEMU command construction: one traversal renders a ``GuestContext`` on a ``HostProfile``.

``QemuCommand.build(host, context)`` assembles the full qemu-system-x86_64 command line -- the
confidential guest, memory, direct boot, PCI topology, networking, volumes and TEE devices -- for
a launch (``LaunchContext``) or the offline measurement dump (``MeasurementContext``). The
traversal is fixed; the context fills each slot.
"""

import re
from dataclasses import dataclass, field

from chutes_cvm.guest.context import IOMMUFD_ID, GuestContext, QemuDevice
from chutes_cvm.guest.host_profile import HostProfile


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


class PcieRootPinning:
    """Assign the launch's emulated virtio devices their pcie.0 slots, in order.

    The boot disk, NIC, volumes and TEE devices occupy slots, and slot layout lands in the DSDT
    and so in RTMR0. QEMU would auto-assign them the lowest free slots anyway, but the command
    states them instead of inheriting an allocator decision: that is what lets offline
    measurement generation reproduce the layout by reading the command rather than re-deriving
    the rule, and a rule derived twice is a rule that can disagree with itself.

    One instance is shared across the builders of a single command -- each call takes the next
    slot, so how many devices there are is the callers' business, not this object's. The run
    starts at 0x1, except under guest NUMA where it starts at 0x2 to keep every emulated device
    below the PXB bridges. Both match live DSDTs from the two paths on one host:
        NUMA  _ADR slots [2,3,4,5,6,7, 24,25, 31]
        FLAT  _ADR slots [1,2,3,4,5,6,  8, 9, 31]

    Adding an emulated device therefore shifts nothing already placed, but it does take the next
    slot and so changes the DSDT -- and with it every published RTMR0.
    """

    _LAST_SLOT = 0x17  # guest-NUMA PXB bridges start at 0x18

    def __init__(self, guest_numa: bool):
        self._next = 0x2 if guest_numa else 0x1

    def device_suffix(self) -> str:
        if self._next > self._LAST_SLOT:
            raise RuntimeError("No free pcie.0 slots for emulated PCI devices")
        addr = self._next
        self._next += 1
        return f",bus=pcie.0,addr=0x{addr:x}"


class PciTopologyState:
    """Tracks PCIe root port allocation across GPUs, NVSwitches, and IB devices."""

    def __init__(self, start_port: int = 16, start_slot: int = 0x8):
        self.port = start_port
        self.slot = start_slot
        self.func = 0

    def add_device(
        self,
        cmd: "QemuCommand",
        endpoint: str,
        *,
        rp_id: str,
        chassis: int,
    ):
        """Place a root port and hang ``endpoint`` off it.

        Owns placement only -- port, slot and function allocation, which is identical whatever
        is being placed. What gets placed is the caller's: a launch hangs a ``vfio-pci`` off it,
        an offline dump a ``pci-bar-stub``. Keeping the two apart is what lets one topology
        walk serve both without either knowing about the other.

        Args:
            cmd: QemuCommand to populate (appends a root port + the endpoint).
            endpoint: the whole ``-device`` argument to hang off this root port.
            rp_id: Root port identifier (e.g. 'rp1', 'rp_nvsw1').
            chassis: Chassis number for the root port.
        """
        if self.func == 0:
            cmd.devices.append(
                f"pcie-root-port,port={self.port},chassis={chassis},id={rp_id},"
                f"bus=pcie.0,multifunction=on,addr={self.slot:#x}"
            )
        else:
            cmd.devices.append(
                f"pcie-root-port,port={self.port},chassis={chassis},id={rp_id},"
                f"bus=pcie.0,addr={self.slot:#x}.{self.func:#x}"
            )

        cmd.devices.append(endpoint)

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

    def _ensure_pxb(self, cmd: "QemuCommand", numa_node: int) -> str:
        if numa_node not in self.pxb_created:
            pxb_id = f"pxb_numa{numa_node}"
            pxb_addr = f"0x{24 + numa_node:x}"
            cmd.devices.append(
                f"pxb-pcie,bus_nr={self.pxb_busnr},id={pxb_id},"
                f"numa_node={numa_node},bus=pcie.0,addr={pxb_addr}"
            )
            self.pxb_created[numa_node] = pxb_id
            self.pxb_port_idx[numa_node] = 0
            self.pxb_busnr += 32
            print(
                f"    Created PXB-PCIe for NUMA node {numa_node} (bus_nr={self.pxb_busnr - 32})"
            )
        return self.pxb_created[numa_node]

    def add_device(
        self,
        cmd: "QemuCommand",
        endpoint: str,
        *,
        rp_id: str,
        chassis: int,
        numa_node: int,
    ):
        """Place a root port under the PXB for numa_node and hang ``endpoint`` off it.

        numa_node is the device's host NUMA node, resolved by the caller (from sysfs for the
        launch path, from the captured device for offline measurement); < 0 (NUMA_NO_NODE — no
        affinity) falls back to flat placement.
        """
        if numa_node < 0:
            self._flat.add_device(
                cmd,
                endpoint,
                rp_id=rp_id,
                chassis=chassis,
            )
            return

        pxb_bus = self._ensure_pxb(cmd, numa_node)
        port_idx = self.pxb_port_idx[numa_node]
        rp_addr = f"0x{port_idx + 1:x}"
        self.pxb_port_idx[numa_node] = port_idx + 1
        cmd.devices.append(
            f"pcie-root-port,port={self.port},chassis={chassis},id={rp_id},bus={pxb_bus},addr={rp_addr}"
        )
        cmd.devices.append(endpoint)
        self.port += 1


@dataclass
class QemuCommand:
    """A structured confidential-guest QEMU command (Intel TDX or AMD SEV-SNP).

    Builders populate the structured fields (``objects``/``numa``/``devices``/…)
    in composition order; ``to_args()`` renders them into the flat
    ``qemu-system-x86_64`` argv in the one canonical section order QEMU needs
    (objects before the -numa that reference them, drives before devices). The
    launcher renders and runs it; offline measurement reads the fields directly
    (no re-parsing) and rewrites them into tdx-measure metadata.

    ``devices`` is a single ordered list: append order sets PCIe slot assignment
    (via PcieRootPinning), so it is preserved verbatim.
    """

    mem: str
    smp_topology: str
    cpu_args: str
    machine: str
    firmware: str
    process_name: str
    foreground: bool
    logfile: str
    pidfile: str
    accel: str = "kvm"
    #: The confidential-guest ``-object``, or None for a command that declares none -- the
    #: offline dump runs plain q35 on a machine with no TEE at all.
    tee_object: "str | None" = None
    # Direct boot (1.4.0+): OVMF boots these kernel/initrd/cmdline directly,
    # dropping GRUB/shim from the measured boot chain. When set, the qcow2 stays
    # attached as the LUKS root but is no longer the boot device (no bootindex).
    # Left None for legacy GRUB boot and the offline ACPI-dump path (rtmr0 is
    # boot-method independent).
    kernel: str | None = None
    initrd: str | None = None
    append: str | None = None
    objects: list[str] = field(default_factory=list)
    numa: list[str] = field(default_factory=list)
    smbios: list[str] = field(default_factory=list)
    drives: list[str] = field(default_factory=list)
    netdevs: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    fw_cfg: list[str] = field(default_factory=list)
    #: ``-serial`` values. A launch derives them from ``foreground``/``logfile``; the dump
    #: attaches ``null`` so COM1 lands in the DSDT.
    serial: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, host: HostProfile, context: GuestContext) -> "QemuCommand":
        """The command ``context`` runs on ``host``: a launch, or the offline measurement dump.

        Pure: reads no live hardware, touches no device.
        """
        pinning = PcieRootPinning(host.uses_guest_numa)
        cmd = _base(host, context, pinning)
        _network(cmd, context, pinning)
        _volumes(cmd, context, pinning)
        _tee_devices(cmd, host, context, pinning)
        _passthrough(cmd, host, context, pinning)
        return cmd

    def add_device(self, device: QemuDevice) -> None:
        """Place one emulated device, and its backing drive if it has one."""
        if device.drive:
            self.drives.append(device.drive)
        self.devices.append(device.device)

    def to_args(self) -> list[str]:
        """Render the flat ``qemu-system-x86_64`` argument list."""
        args = [
            "qemu-system-x86_64",
            "-accel",
            self.accel,
            "-m",
            self.mem,
            "-smp",
            self.smp_topology,
            "-name",
            f"{self.process_name},process={self.process_name},debug-threads=on",
            "-cpu",
            self.cpu_args,
        ]
        if self.tee_object:
            args += ["-object", self.tee_object]
        for o in self.objects:
            args += ["-object", o]
        for n in self.numa:
            args += ["-numa", n]
        args += [
            "-machine",
            self.machine,
            "-bios",
            self.firmware,
            "-nodefaults",
            "-vga",
            "none",
        ]
        for s in self.smbios:
            args += ["-smbios", s]
        args.append("-nographic")
        for sr in self.serial:
            args += ["-serial", sr]
        if not self.foreground:
            args += ["-daemonize", "-pidfile", self.pidfile]
        if self.kernel:
            args += ["-kernel", self.kernel]
        if self.initrd:
            args += ["-initrd", self.initrd]
        if self.append is not None:
            args += ["-append", self.append]
        for d in self.drives:
            args += ["-drive", d]
        for nd in self.netdevs:
            args += ["-netdev", nd]
        for dev in self.devices:
            args += ["-device", dev]
        for fc in self.fw_cfg:
            args += ["-fw_cfg", fc]
        return args


# ── the traversal: one fixed walk for every kind of guest ─────────────────────────────────────
#
# ``QemuCommand.build`` walks a fixed sequence -- base, network, volumes, TEE devices,
# passthrough -- claiming pcie.0 slots from ONE ``PcieRootPinning`` as it goes. Slot layout lands
# in the DSDT and so in RTMR0, so that walk, its order and its count live here once; a
# ``GuestContext`` chooses only what string goes in each slot.
#
# That is the whole of the launch/measurement difference, and it is expressed by construction
# rather than by rewriting afterwards. The offline path used to build a launch-shaped command and
# let ``ImageConfig`` substitute seven things over it, which is brittle in one direction only:
# anything added to the launch command and not stripped there silently entered the bytes the fork
# hashes. Here a new emulated device gets a slot in BOTH commands because there is one call site.


def _base(
    host: HostProfile, context: GuestContext, pinning: PcieRootPinning
) -> QemuCommand:
    """Confidential guest, firmware, CPU, memory, direct boot, and the root disk."""
    profile = host
    host_nodes = list(context.host_nodes)
    process = context.process
    img_path = context.image
    # The profile is the single authority on guest shape: memory, -smp, -cpu, the platform,
    # and whether this class runs a NUMA guest. Deriving NUMA from len(host_nodes) instead
    # meant two sources of truth -- a host whose live sysfs disagreed with its captured
    # profile got PXB bridges (from the profile) with flat memory args (from sysfs), a
    # command matching no measurement.
    numa_enabled = profile.uses_guest_numa
    if numa_enabled != (len(host_nodes) >= 2):
        raise ValueError(
            f"host_nodes {host_nodes} does not match the profile's guest-NUMA setting "
            f"(uses_guest_numa={numa_enabled}). The profile decides the guest shape; a host "
            "whose live NUMA disagrees with its captured profile cannot launch a guest any "
            "measurement was generated for."
        )

    cmd = QemuCommand(
        mem=profile.mem,
        smp_topology=profile.smp_topology,
        cpu_args=context.cpu_args(host),
        # A flat guest binds the machine to its single backend; a NUMA guest gets one per
        # node below and binds none here.
        machine=context.machine(host, None if numa_enabled else "mem0"),
        firmware=context.firmware,
        process_name=process.name,
        foreground=process.foreground,
        logfile=process.logfile,
        pidfile=process.pidfile,
        serial=context.serial(),
        tee_object=context.tee_object(host),
        # Pinned SMBIOS identity so per-server motherboard differences don't shift RTMR0
        # within a profile.
        smbios=_smbios(host),
    )

    if numa_enabled:
        mem_mib = _parse_mem_mib(profile.mem)
        _numa_memory(cmd, host, context, mem_mib, host_nodes)
        print(
            f"NUMA: {len(host_nodes)} guest nodes, "
            f"{mem_mib // len(host_nodes)}M each (approx), host nodes {host_nodes}"
        )
    else:
        cmd.objects.append(context.memory_backend(host, "mem0", profile.mem))

    # Direct boot (always): OVMF loads the kernel/initrd/cmdline itself. The qcow2 is still
    # the LUKS root, just not the boot device -- so no bootindex.
    cmd.kernel = context.boot.kernel
    cmd.initrd = context.boot.initrd
    cmd.append = context.kernel_cmdline

    img_fmt = _block_format(img_path)
    drive = f"file={img_path},if=none,id=virtio-disk0,cache=none,aio=native,format={img_fmt}"
    if img_fmt == "qcow2":
        drive += ",discard=unmap"
    elif img_fmt == "raw":
        drive += ",discard=on,detect-zeroes=on"
    cmd.add_device(
        context.qemu_device(
            device="virtio-blk-pci,drive=virtio-disk0",
            slot=pinning.device_suffix(),
            drive=drive,
            opts=",num-queues=4" if img_fmt == "raw" else "",
        )
    )
    return cmd


def _smbios(host: HostProfile) -> list[str]:
    product = host.tee_provider.smbios_product
    return [
        f"type=1,manufacturer=Chutes,product={product},version=1.0,serial=0,"
        "uuid=00000000-0000-0000-0000-000000000000",
        f"type=2,manufacturer=Chutes,product={product},version=1.0,serial=0",
        "type=3,manufacturer=Chutes,version=1.0,serial=0",
    ]


def _numa_memory(
    cmd: QemuCommand,
    host: HostProfile,
    context: GuestContext,
    mem_mib: int,
    host_nodes: list[int],
) -> None:
    """One memory backend per guest NUMA node, bound to its host node.

    NB: never set prealloc=on. Guest RAM is private memory allocated as the guest accepts
    pages; preallocating pins a second full copy the guest never uses, roughly doubling host
    memory use and OOM-killing the host during pod warmup.
    """
    num_nodes = len(host_nodes)
    per_node_mib = mem_mib // num_nodes
    for i, hnode in enumerate(host_nodes):
        node_size_mib = (
            mem_mib - per_node_mib * (num_nodes - 1)
            if i == num_nodes - 1
            else per_node_mib
        )
        cmd.objects.append(
            context.memory_backend(
                host, f"mem-node{i}", f"{node_size_mib}M", host_node=hnode
            )
        )
        cmd.numa.append(f"node,nodeid={i},memdev=mem-node{i}")
        cmd.numa.append(f"cpu,node-id={i},socket-id={i}")
    if num_nodes == 2:
        cmd.numa.append("dist,src=0,dst=1,val=21")


def _network(cmd: QemuCommand, context: GuestContext, pinning: PcieRootPinning) -> None:
    """The guest's one NIC.

    A launch always has exactly one, so the device is unconditional; the ``-netdev`` backing
    it follows the inputs. In tap mode without a host interface the device is emitted alone,
    which is what offline generation wants: the device occupies a measured pcie.0 slot, while
    a netdev is not a PCI device and is not measured.
    """
    net = context.network
    if net.network_type == "tap":
        vectors = 2 * net.net_queues + 2
        cmd.add_device(
            context.qemu_device(
                device=(
                    "virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56,mq=on,"
                    f"vectors={vectors},mrg_rxbuf=on"
                ),
                slot=pinning.device_suffix(),
            )
        )
        if net.net_iface:
            print(
                f"Networking: TAP mode (iface={net.net_iface}, "
                f"queues={net.net_queues}, vhost=on)"
            )
            cmd.netdevs.append(
                f"tap,id=n0,ifname={net.net_iface},script=no,downscript=no,"
                f"vhost=on,queues={net.net_queues}"
            )
    else:
        print("Networking: Canonical user-mode networking")
        cmd.add_device(
            context.qemu_device(
                device="virtio-net-pci,netdev=nic0_td", slot=pinning.device_suffix()
            )
        )
        cmd.netdevs.append(f"user,id=nic0_td,hostfwd=tcp::{net.ssh_port}-:22")


def _volumes(cmd: QemuCommand, context: GuestContext, pinning: PcieRootPinning) -> None:
    """Config, cache and storage, in that order -- the order assigns their slots."""
    volumes = context.volumes
    if volumes.config:
        cmd.add_device(
            context.qemu_device(
                device="virtio-blk-pci,drive=virtio-config",
                slot=pinning.device_suffix(),
                drive=(
                    f"file={volumes.config},if=none,id=virtio-config,cache=none,"
                    "format=qcow2,readonly=on"
                ),
            )
        )
    for path, vol_id in (
        (volumes.cache, "virtio-cache"),
        (volumes.storage, "virtio-storage"),
    ):
        if not path:
            continue
        fmt = _block_format(path)
        drive = f"file={path},if=none,id={vol_id},cache=none,aio=native,format={fmt}"
        if fmt == "raw":
            drive += ",discard=on,detect-zeroes=on"
        cmd.add_device(
            context.qemu_device(
                device=f"virtio-blk-pci,drive={vol_id}",
                slot=pinning.device_suffix(),
                drive=drive,
                opts=",num-queues=4" if fmt == "raw" else "",
            )
        )


def _tee_devices(
    cmd: QemuCommand, host: HostProfile, context: GuestContext, pinning: PcieRootPinning
) -> None:
    for device in host.tee_provider.devices():
        cmd.add_device(context.qemu_device(device=device, slot=pinning.device_suffix()))


def _passthrough(
    cmd: QemuCommand, host: HostProfile, context: GuestContext, pinning: PcieRootPinning
) -> None:
    """Root ports and their endpoints.

    Each device's NUMA node comes from the capture it was read from -- never from a second
    read of the live machine, which is how the launch and the measurement came to disagree
    about where a GPU sat. Root ports are numbered per kind in device order (``rp1..rpN``
    for GPUs, then ``rp_nvsw*``, then ``rp_ib*``) and chassis numbers run across all of
    them, which is the ordering the guest PXB grouping and therefore RTMR0 depend on.
    """
    passthrough = context.passthrough
    devices = (
        ("rp", passthrough.gpus),
        ("rp_nvsw", passthrough.nvswitches),
        ("rp_ib", passthrough.ib),
    )
    if not any(group for _, group in devices):
        return

    if context.wants_iommufd():
        # Every endpoint carries iommufd=<id>, so a command with passthrough devices and no
        # such object is one QEMU refuses. The two are one decision.
        cmd.objects.append(f"iommufd,id={IOMMUFD_ID}")

    guest_numa = host.uses_guest_numa
    topo: "PciTopologyState | NumaPciTopologyState"
    if guest_numa:
        print("  PCI topology: NUMA-local PXB-PCIe bridges")
        topo = NumaPciTopologyState()
    else:
        topo = PciTopologyState()

    print(f"  Adding {len(passthrough.gpus)} GPU(s) to PCI topology...")
    # OVMF sizes the guest's 64-bit MMIO window from the BARs it enumerates; nothing is pinned.
    print("    MMIO: OVMF auto-sizes the 64-bit window from the passed-through BARs")
    chassis = 0
    for prefix, group in devices:
        for ordinal, device in enumerate(group, start=1):
            chassis += 1
            rp_id = f"{prefix}{ordinal}"
            placement = {"numa_node": device.numa_node} if guest_numa else {}
            topo.add_device(
                cmd,
                context.endpoint(host, device, rp_id),
                rp_id=rp_id,
                chassis=chassis,
                **placement,
            )
            if guest_numa and device.numa_node >= 0:
                print(f"    {device.bdf} -> PXB NUMA node {device.numa_node}")
    print(
        f"  Passthrough configured: {len(passthrough.gpus)} GPU(s), "
        f"{len(passthrough.nvswitches)} NVSwitch(es), {len(passthrough.ib)} IB device(s)"
    )
