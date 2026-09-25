"""QEMU command construction for TDX VM launch.

Builds the full qemu-system-x86_64 command line including base TDX
configuration, PCI device topology, networking, volumes, and vsock.
"""

import os
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field

from chutes_cvm.guest.devices import PciDevice
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.tee import TeeProvider


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


#: The iommufd object id every vfio-pci endpoint references. Spelled once: an endpoint naming an
#: id no object declares is a command QEMU refuses, and it used to be written out in three places.
IOMMUFD_ID = "iommufd0"


class PcieRootPinning:
    """Assign the launch's emulated virtio devices their pcie.0 slots, in order.

    The boot disk, NIC, volumes and vsock occupy slots, and slot layout lands in the DSDT and so
    in RTMR0. QEMU would auto-assign them the lowest free slots anyway, but the command states
    them instead of inheriting an allocator decision: that is what lets offline measurement
    generation reproduce the layout by reading the command rather than re-deriving the rule, and
    a rule derived twice is a rule that can disagree with itself.

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


def read_pci_numa_node(bdf: str) -> int:
    """Read PCI device NUMA node from sysfs. Returns -1 if unknown."""
    path = f"/sys/bus/pci/devices/{bdf}/numa_node"
    try:
        with open(path) as f:
            node = int(f.read().strip())
    except (OSError, ValueError):
        return -1
    return node if node >= 0 else -1


def _append_numa_memory(
    cmd: "QemuCommand", mem_mib: int, host_nodes: list[int], tee: "TeeProvider"
) -> None:
    """Add per-node memory backends and guest NUMA topology to ``cmd``.

    NB: do NOT set prealloc=on on these backends. Under TDX
    (confidential-guest-support=tdx) the guest's actual RAM is private memory
    served from guest_memfd, allocated lazily as the guest accepts pages.
    Preallocating the memory-backend pins a second full copy of pages the guest
    never uses as shared, doubling host memory consumption (~2x guest RAM) and
    OOM-killing the host during pod warmup. host-nodes/policy=bind keeps the
    lazy allocations NUMA-local, which is the only reason these backends exist.
    """
    num_nodes = len(host_nodes)
    per_node_mib = mem_mib // num_nodes
    for i, hnode in enumerate(host_nodes):
        if i == num_nodes - 1:
            node_size_mib = mem_mib - per_node_mib * (num_nodes - 1)
        else:
            node_size_mib = per_node_mib
        cmd.objects.append(
            tee.memory_backend(f"mem-node{i}", f"{node_size_mib}M", host_node=hnode)
        )
        cmd.numa.append(f"node,nodeid={i},memdev=mem-node{i}")
        cmd.numa.append(f"cpu,node-id={i},socket-id={i}")

    if num_nodes == 2:
        cmd.numa.append("dist,src=0,dst=1,val=21")


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


def build_pci_topology(
    cmd: "QemuCommand",
    *,
    gpus: "Sequence[PciDevice]",
    nvswitches: "Sequence[PciDevice]",
    ib_devices: "Sequence[PciDevice]",
    guest_numa: bool,
) -> None:
    """Add every passthrough endpoint to the command's PCI topology.

    Takes the devices, not a host: the caller decides which ones the guest gets (NVSwitch and IB
    are gated by the GPU profile) and this places them. Each device's NUMA node comes from the
    capture it was read from -- never from a second read of the live machine, which is how the
    launch and the measurement came to disagree about where a GPU sat.

    ``guest_numa`` must agree with the memory topology, because PXB bridges name guest NUMA
    nodes; building them for a guest with no ``-numa`` makes QEMU refuse with "Illegal numa
    node 0".

    NB: when IB passthrough is enabled, a launch attaches the SR-IOV VFs it creates, not the PFs
    the profile captured. Nothing passes IB through today, so both are empty and it is open.
    """
    topo: "PciTopologyState | NumaPciTopologyState"
    if guest_numa:
        print("  PCI topology: NUMA-local PXB-PCIe bridges")
        topo = NumaPciTopologyState()
    else:
        topo = PciTopologyState()

    print(f"  Adding {len(gpus)} GPU(s) to PCI topology...")
    # OVMF sizes the guest's 64-bit MMIO window from the BARs it enumerates; nothing is pinned.
    print("    MMIO: OVMF auto-sizes the 64-bit window from the passed-through BARs")
    chassis = 0
    for prefix, devices in (
        ("rp", gpus),
        ("rp_nvsw", nvswitches),
        ("rp_ib", ib_devices),
    ):
        for ordinal, device in enumerate(devices, start=1):
            chassis += 1
            rp_id = f"{prefix}{ordinal}"
            placement = {"numa_node": device.numa_node} if guest_numa else {}
            topo.add_device(
                cmd,
                f"vfio-pci,host={device.bdf},bus={rp_id},addr=0x0,iommufd={IOMMUFD_ID}",
                rp_id=rp_id,
                chassis=chassis,
                **placement,
            )
            if guest_numa and device.numa_node >= 0:
                print(f"    {device.bdf} -> PXB NUMA node {device.numa_node}")

    print(
        f"  Passthrough configured: {len(gpus)} GPU(s), "
        f"{len(nvswitches)} NVSwitch(es), {len(ib_devices)} IB device(s)"
    )


@dataclass(frozen=True)
class DirectBoot:
    """The kernel, initrd and cmdline OVMF boots directly, dropping GRUB from the measured chain.

    ``direct_boot_artifacts()`` already returns exactly this triple. The offline ACPI-dump path
    passes placeholders: RTMR0 is boot-method independent and the measured tables carry no kernel.
    """

    kernel: str
    initrd: str
    cmdline: str


@dataclass(frozen=True)
class GuestNetwork:
    """The guest's one NIC. A launch resolves these; measurement uses the tap shape with no
    interface, because the device occupies a measured pcie.0 slot while the netdev backing it
    is not a PCI device and is not measured."""

    network_type: str
    net_iface: "str | None" = None
    ssh_port: int = 0
    net_queues: int = 4


@dataclass(frozen=True)
class GuestVolumes:
    """The volumes attached beside the root image.

    Named, not ``VolumeSpec`` -- ``guest.config.VolumeSpec`` is the operator's declared size and
    path. These are the resolved paths a command attaches. Measurement passes the canonical
    filenames: the drives are replaced by backing-free fillers before the dump, but the pcie.0
    slots they occupy land in the DSDT.
    """

    config: "str | None" = None
    cache: "str | None" = None
    storage: "str | None" = None


@dataclass(frozen=True)
class PassthroughSet:
    """The devices the guest gets, as the command names them.

    Defaults to the profile's own lists, which is what both a normal launch and offline
    generation want. A ``--no-gpus`` debug launch passes an empty set: the GPUs are not bound to
    vfio-pci, so naming them would build a command QEMU refuses.

    It is a value rather than a boolean because the launch set will not always equal the
    profile's -- when IB passthrough is enabled a launch attaches the SR-IOV VFs that
    ``bind_passthrough`` creates, not the PFs the profile captured.
    """

    gpus: "Sequence[PciDevice]" = ()
    nvswitches: "Sequence[PciDevice]" = ()
    ib: "Sequence[PciDevice]" = ()

    @classmethod
    def from_profile(cls, profile: HostProfile) -> "PassthroughSet":
        """The devices the captured profile says this class attaches.

        Not ``from_host``: that name is taken by ``HostProfile.from_host``, which runs
        discover-profile.sh and reads the live machine. This reads nothing.
        """
        return cls(profile.gpus, profile.attached_nvswitches, profile.attached_ib)


@dataclass(frozen=True)
class ProcessBundle:
    """What a running QEMU is called and where it writes.

    Exactly the fields ``to_args()`` spends on ``-name``, daemonize, ``-D`` and ``-pidfile`` --
    and exactly the ones ``ImageConfig.to_dict()`` drops. That is why they are their own bundle
    rather than part of the boot artifacts: a measurement has no process.
    """

    name: str
    foreground: bool = False
    pidfile: str = "/dev/null"
    logfile: str = "/dev/null"


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
    def create(cls, profile: HostProfile, **inputs) -> "QemuCommand":
        """The command a launch on ``profile`` runs. See ``LaunchCommandBuilder``."""
        return LaunchCommandBuilder(profile).build(**inputs)

    @classmethod
    def for_measurement(cls, profile: HostProfile, *, firmware: str) -> "QemuCommand":
        """The command offline generation measures. See ``MeasurementCommandBuilder``."""
        return MeasurementCommandBuilder(profile).build(
            firmware=firmware,
            img_path="root.qcow2",
            host_nodes=[0, 1] if profile.uses_guest_numa else [],
            boot=DirectBoot(kernel="/dev/null", initrd="/dev/null", cmdline=""),
            net=GuestNetwork(network_type="tap", net_iface=None, ssh_port=0),
            volumes=GuestVolumes(
                config="config.qcow2", cache="cache.raw", storage="storage.raw"
            ),
            process=ProcessBundle(name="chutes-measure"),
            passthrough=PassthroughSet.from_profile(profile),
        )

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


class QemuCommandBuilder(ABC):
    """One traversal, two purposes.

    ``build`` walks a fixed sequence -- base, network, volumes, vsock, passthrough -- claiming
    pcie.0 slots from ONE ``PcieRootPinning`` as it goes. Slot layout lands in the DSDT and so in
    RTMR0, so that walk, its order and its count live here once and subclasses cannot reach them.
    All a subclass chooses is what string goes in each slot.

    That is the whole of the launch/measurement difference, and it is expressed by construction
    rather than by rewriting afterwards. The offline path used to build a launch-shaped command
    and let ``ImageConfig`` substitute seven things over it, which is brittle in one direction
    only: anything added to the launch command and not stripped there silently entered the bytes
    the fork hashes. Here a new emulated device gets a slot in BOTH commands because there is one
    call site, and the only way to break it is to leave an abstract method unimplemented.
    """

    def __init__(self, profile: HostProfile):
        self.profile = profile

    # ── the traversal: shared, ordered, and not overridable ─────────────────────────────
    def build(
        self,
        *,
        firmware: str,
        img_path: str,
        host_nodes: list[int],
        boot: DirectBoot,
        net: GuestNetwork,
        volumes: GuestVolumes,
        process: ProcessBundle,
        passthrough: PassthroughSet,
    ) -> QemuCommand:
        """Assemble the whole command. Pure: reads no live hardware, touches no device."""
        pinning = PcieRootPinning(self.profile.uses_guest_numa)
        cmd = self._base(
            firmware=firmware,
            img_path=img_path,
            host_nodes=host_nodes,
            boot=boot,
            process=process,
            pinning=pinning,
        )
        self._network(cmd, net, pinning)
        self._volumes(cmd, volumes, pinning)
        self._vsock(cmd, pinning)
        self._passthrough(cmd, passthrough, pinning)
        return cmd

    # ── leaves: the only variation ──────────────────────────────────────────────────────
    @abstractmethod
    def machine(self, flat_backend: "str | None") -> str:
        """The ``-machine`` argument. ``flat_backend`` names the single memory backend a flat
        guest binds, or is None under guest NUMA where per-node memdevs bind instead."""

    @abstractmethod
    def memory_backend(
        self, backend_id: str, size: str, host_node: "int | None" = None
    ) -> str:
        """One ``-object`` memory backend."""

    @abstractmethod
    def cpu_args(self) -> str:
        """The ``-cpu`` string."""

    @abstractmethod
    def tee_object(self) -> "str | None":
        """The confidential-guest ``-object``, or None for a command that declares none."""

    @abstractmethod
    def serial(self, process: ProcessBundle) -> list[str]:
        """The ``-serial`` values."""

    @abstractmethod
    def emit(
        self,
        cmd: QemuCommand,
        *,
        device: str,
        slot: str,
        drive: "str | None" = None,
        opts: str = "",
    ) -> None:
        """Place one emulated device at ``slot``, with its backing drive if it has one.

        ``opts`` are device options that must follow the address. QEMU does not care -- device
        options are comma-separated and order-independent -- but it is the order every published
        measurement was generated against, so it is preserved rather than tidied.
        """

    @abstractmethod
    def endpoint(self, device: PciDevice, rp_id: str) -> str:
        """The ``-device`` argument for one passthrough endpoint on ``rp_id``."""

    @abstractmethod
    def wants_iommufd(self) -> bool:
        """Whether the endpoints this builder emits reference an iommufd object."""

    # ── traversal steps ─────────────────────────────────────────────────────────────────
    def _base(
        self,
        *,
        firmware: str,
        img_path: str,
        host_nodes: list[int],
        boot: DirectBoot,
        process: ProcessBundle,
        pinning: PcieRootPinning,
    ) -> QemuCommand:
        """Confidential guest, firmware, CPU, memory, direct boot, and the root disk."""
        profile = self.profile
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
            cpu_args=self.cpu_args(),
            # A flat guest binds the machine to its single backend; a NUMA guest gets one per
            # node below and binds none here.
            machine=self.machine(None if numa_enabled else "mem0"),
            firmware=firmware,
            process_name=process.name,
            foreground=process.foreground,
            logfile=process.logfile,
            pidfile=process.pidfile,
            serial=self.serial(process),
            tee_object=self.tee_object(),
            # Pinned SMBIOS identity so per-server motherboard differences don't shift RTMR0
            # within a profile.
            smbios=self._smbios(),
        )

        if numa_enabled:
            mem_mib = _parse_mem_mib(profile.mem)
            self._numa_memory(cmd, mem_mib, host_nodes)
            print(
                f"NUMA: {len(host_nodes)} guest nodes, "
                f"{mem_mib // len(host_nodes)}M each (approx), host nodes {host_nodes}"
            )
        else:
            cmd.objects.append(self.memory_backend("mem0", profile.mem))

        # Direct boot (always): OVMF loads the kernel/initrd/cmdline itself. The qcow2 is still
        # the LUKS root, just not the boot device -- so no bootindex.
        cmd.kernel = boot.kernel
        cmd.initrd = boot.initrd
        cmd.append = boot.cmdline

        img_fmt = _block_format(img_path)
        drive = f"file={img_path},if=none,id=virtio-disk0,cache=none,aio=native,format={img_fmt}"
        if img_fmt == "qcow2":
            drive += ",discard=unmap"
        elif img_fmt == "raw":
            drive += ",discard=on,detect-zeroes=on"
        self.emit(
            cmd,
            device="virtio-blk-pci,drive=virtio-disk0",
            slot=pinning.device_suffix(),
            drive=drive,
            opts=",num-queues=4" if img_fmt == "raw" else "",
        )
        return cmd

    def _smbios(self) -> list[str]:
        product = self.profile.tee_provider.smbios_product
        return [
            f"type=1,manufacturer=Chutes,product={product},version=1.0,serial=0,"
            "uuid=00000000-0000-0000-0000-000000000000",
            f"type=2,manufacturer=Chutes,product={product},version=1.0,serial=0",
            "type=3,manufacturer=Chutes,version=1.0,serial=0",
        ]

    def _numa_memory(
        self, cmd: QemuCommand, mem_mib: int, host_nodes: list[int]
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
                self.memory_backend(
                    f"mem-node{i}", f"{node_size_mib}M", host_node=hnode
                )
            )
            cmd.numa.append(f"node,nodeid={i},memdev=mem-node{i}")
            cmd.numa.append(f"cpu,node-id={i},socket-id={i}")
        if num_nodes == 2:
            cmd.numa.append("dist,src=0,dst=1,val=21")

    def _network(
        self, cmd: QemuCommand, net: GuestNetwork, pinning: PcieRootPinning
    ) -> None:
        """The guest's one NIC.

        A launch always has exactly one, so the device is unconditional; the ``-netdev`` backing
        it follows the inputs. In tap mode without a host interface the device is emitted alone,
        which is what offline generation wants: the device occupies a measured pcie.0 slot, while
        a netdev is not a PCI device and is not measured.
        """
        if net.network_type == "tap":
            vectors = 2 * net.net_queues + 2
            self.emit(
                cmd,
                device=(
                    "virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56,mq=on,"
                    f"vectors={vectors},mrg_rxbuf=on"
                ),
                slot=pinning.device_suffix(),
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
            self.emit(
                cmd,
                device="virtio-net-pci,netdev=nic0_td",
                slot=pinning.device_suffix(),
            )
            cmd.netdevs.append(f"user,id=nic0_td,hostfwd=tcp::{net.ssh_port}-:22")

    def _volumes(
        self, cmd: QemuCommand, volumes: GuestVolumes, pinning: PcieRootPinning
    ) -> None:
        """Config, cache and storage, in that order -- the order assigns their slots."""
        if volumes.config:
            self.emit(
                cmd,
                device="virtio-blk-pci,drive=virtio-config",
                slot=pinning.device_suffix(),
                drive=(
                    f"file={volumes.config},if=none,id=virtio-config,cache=none,"
                    "format=qcow2,readonly=on"
                ),
            )
        for path, vol_id in (
            (volumes.cache, "virtio-cache"),
            (volumes.storage, "virtio-storage"),
        ):
            if not path:
                continue
            fmt = _block_format(path)
            drive = (
                f"file={path},if=none,id={vol_id},cache=none,aio=native,format={fmt}"
            )
            if fmt == "raw":
                drive += ",discard=on,detect-zeroes=on"
            self.emit(
                cmd,
                device=f"virtio-blk-pci,drive={vol_id}",
                slot=pinning.device_suffix(),
                drive=drive,
                opts=",num-queues=4" if fmt == "raw" else "",
            )

    def _vsock(self, cmd: QemuCommand, pinning: PcieRootPinning) -> None:
        self.emit(
            cmd, device="vhost-vsock-pci,guest-cid=3", slot=pinning.device_suffix()
        )

    def _passthrough(
        self, cmd: QemuCommand, passthrough: PassthroughSet, pinning: PcieRootPinning
    ) -> None:
        """Root ports and their endpoints.

        Each device's NUMA node comes from the capture it was read from -- never from a second
        read of the live machine, which is how the launch and the measurement came to disagree
        about where a GPU sat. Root ports are numbered per kind in device order (``rp1..rpN``
        for GPUs, then ``rp_nvsw*``, then ``rp_ib*``) and chassis numbers run across all of
        them, which is the ordering the guest PXB grouping and therefore RTMR0 depend on.
        """
        devices = (
            ("rp", passthrough.gpus),
            ("rp_nvsw", passthrough.nvswitches),
            ("rp_ib", passthrough.ib),
        )
        if not any(group for _, group in devices):
            return

        if self.wants_iommufd():
            # Every endpoint carries iommufd=<id>, so a command with passthrough devices and no
            # such object is one QEMU refuses. The two are one decision.
            cmd.objects.append(f"iommufd,id={IOMMUFD_ID}")

        guest_numa = self.profile.uses_guest_numa
        topo: "PciTopologyState | NumaPciTopologyState"
        if guest_numa:
            print("  PCI topology: NUMA-local PXB-PCIe bridges")
            topo = NumaPciTopologyState()
        else:
            topo = PciTopologyState()

        print(f"  Adding {len(passthrough.gpus)} GPU(s) to PCI topology...")
        # OVMF sizes the guest's 64-bit MMIO window from the BARs it enumerates; nothing is pinned.
        print(
            "    MMIO: OVMF auto-sizes the 64-bit window from the passed-through BARs"
        )
        chassis = 0
        for prefix, group in devices:
            for ordinal, device in enumerate(group, start=1):
                chassis += 1
                rp_id = f"{prefix}{ordinal}"
                placement = {"numa_node": device.numa_node} if guest_numa else {}
                topo.add_device(
                    cmd,
                    self.endpoint(device, rp_id),
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


class LaunchCommandBuilder(QemuCommandBuilder):
    """The command this host actually runs.

    Every leaf comes from the profile's ``TeeProvider`` or the profile itself: the confidential
    guest, the memory backend type, the SMBIOS product. Nothing here knows about measurement.
    """

    def machine(self, flat_backend: "str | None") -> str:
        return self.profile.tee_provider.machine(flat_backend)

    def memory_backend(
        self, backend_id: str, size: str, host_node: "int | None" = None
    ) -> str:
        return self.profile.tee_provider.memory_backend(
            backend_id, size, host_node=host_node
        )

    def cpu_args(self) -> str:
        return self.profile.cpu_args

    def tee_object(self) -> "str | None":
        return self.profile.tee_provider.guest_object()

    def serial(self, process: ProcessBundle) -> list[str]:
        return ["mon:stdio"] if process.foreground else [f"file:{process.logfile}"]

    def emit(
        self,
        cmd: QemuCommand,
        *,
        device: str,
        slot: str,
        drive: "str | None" = None,
        opts: str = "",
    ) -> None:
        if drive:
            cmd.drives.append(drive)
        cmd.devices.append(f"{device}{slot}{opts}")

    def endpoint(self, device: PciDevice, rp_id: str) -> str:
        return f"vfio-pci,host={device.bdf},bus={rp_id},addr=0x0,iommufd={IOMMUFD_ID}"

    def wants_iommufd(self) -> bool:
        return True


class MeasurementCommandBuilder(QemuCommandBuilder):
    """The command offline generation measures, built for that purpose rather than rewritten.

    Every leaf answers "what reproduces the launch's measured ACPI on a box with no TEE, no GPUs
    and less RAM than the guest". Sits beside ``LaunchCommandBuilder`` on purpose: the seven
    differences between a launch and a measurement are the seven method bodies, and a reader can
    diff them without opening another file.
    """

    #: The dumper runs plain q35: the ACPI tables are identical and the container's QEMU has no
    #: confidential-guest support at all.
    DUMP_MACHINE = "q35,kernel_irqchip=split,smm=off,pic=off"

    def machine(self, flat_backend: "str | None") -> str:
        # A flat topology wires guest RAM to a machine-level backend; without it QEMU falls back
        # to allocating the full pc.ram, which a small generating host cannot back (the mem0
        # object carries reserve=off).
        if flat_backend:
            return f"{self.DUMP_MACHINE},memory-backend={flat_backend}"
        return self.DUMP_MACHINE

    def memory_backend(
        self, backend_id: str, size: str, host_node: "int | None" = None
    ) -> str:
        """The launch's backend type, unbound and unreserved.

        ``reserve=off`` maps any-size guest RAM on a small host without allocating it, and the
        host-NUMA binding is dropped because the generating box's nodes are not the launch host's.
        """
        provider = self.profile.tee_provider
        parts = [provider.memory_backend_type, f"id={backend_id}", f"size={size}"]
        parts.extend(provider.memory_backend_opts)
        parts.append("reserve=off")
        return ",".join(parts)

    def cpu_args(self) -> str:
        """The launch ``-cpu`` plus an explicit CPU identity.

        ``vendor`` fixes the SRAT memory hole (AMD-guest-gated); the SMBIOS Type-4 Processor ID is
        patched separately by tdx-measure. BOTH must be set, so a host with neither captured is
        refused rather than measured as the generating host's CPU.
        """
        cpu = self.profile.cpu
        if not cpu.vendor or cpu.processor_id is None:
            raise ValueError(
                f"host class {self.profile.variant_label!r} has no captured CPU model "
                "(processor_id is None); offline RTMR0 would be generated for the generating "
                "host's CPU. Re-register from a host of this class with a current chutes-cvm."
            )
        return f"{self.profile.cpu_args},vendor={cpu.vendor}"

    def tee_object(self) -> "str | None":
        return None

    def serial(self, process: ProcessBundle) -> list[str]:
        return ["null"]  # adds COM1 to the DSDT

    def emit(
        self,
        cmd: QemuCommand,
        *,
        device: str,
        slot: str,
        drive: "str | None" = None,
        opts: str = "",
    ) -> None:
        """A backing-free filler at the slot this device was given.

        The dump has no drives or netdevs to reference, but the slot lands in the DSDT and so in
        RTMR0 -- so the slot is kept and only the backing goes.
        """
        cmd.devices.append(f"virtio-rng-pci{slot}")

    def endpoint(self, device: PciDevice, rp_id: str) -> str:
        """A ``pci-bar-stub`` carrying this device's own BAR layout.

        The stub reproduces the MMIO windows the real BARs would create, which is what OVMF sizes
        the guest's 64-bit aperture from. Per-device rather than one representative per kind, so a
        GPU model is measured from a submitted profile rather than a transcribed table.
        """
        if not device.bars:
            raise ValueError(
                f"no BARs captured for {rp_id!r} on a "
                f"{self.profile.gpu_profile.name!r} host. The stub reproduces the guest's MMIO "
                "windows from them, so without them the generated RTMR0 matches no real boot. "
                "Re-submit this host's profile with a current chutes-cvm."
            )
        bars = ";".join(b.as_stub_arg() for b in device.bars)
        return (
            f"pci-bar-stub,bus={rp_id},bars={bars},"
            f"vendor={int(device.vendor, 16):#06x},"
            f"device={int(device.device_id, 16):#06x},"
            f"class={int(device.pci_class, 16):#06x}"
        )

    def wants_iommufd(self) -> bool:
        # Nothing in the dump references it: every endpoint is a stub, not a vfio-pci.
        return False
