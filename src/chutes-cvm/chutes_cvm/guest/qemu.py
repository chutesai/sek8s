"""QEMU command construction for TDX VM launch.

Builds the full qemu-system-x86_64 command line including base TDX
configuration, PCI device topology, networking, volumes, and vsock.
"""

import os
import re
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
    # The confidential-guest -object. Defaults to TDX so anything constructing a
    # QemuCommand directly (offline measurement included) is unchanged; the launcher
    # and build_base_cmd set it from the detected platform's TeeProvider.
    tee_object: str = (
        '{"qom-type":"tdx-guest","id":"tdx",'
        '"quote-generation-socket":{"type":"vsock","cid":"2","port":"4050"}}'
    )
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

    @classmethod
    def create(
        cls,
        profile: HostProfile,
        *,
        firmware: str,
        img_path: str,
        host_nodes: list[int],
        boot: DirectBoot,
        net: GuestNetwork,
        volumes: GuestVolumes,
        process: ProcessBundle,
        passthrough: PassthroughSet,
    ) -> "QemuCommand":
        """The whole command, assembled in one place from resolved inputs.

        The five steps below each claim pcie.0 slots from ONE ``PcieRootPinning``, and slot layout
        lands in the DSDT and so in RTMR0. That allocator is created and consumed here precisely so
        no caller can run the steps in a different order, or one too many times, and silently shift
        every published measurement. It used to be four separately callable builders invoked by two
        hand-written call sites, with a parity test as the only thing holding them in step.

        Pure: reads no live hardware and touches no device, which is what lets offline measurement
        generation share it. Binding is ``passthrough.bind_passthrough`` and runs before this.

        Nothing is optional. Every argument is a decision that differs between a launch and a
        measurement, so a default here would be one of the two shapes silently standing in for the
        other -- which is how the launcher came to pass a bare ``-cpu`` constant while generation
        used the profile's.
        """
        # One instance across every step, as a launch does: under guest NUMA it starts at slot
        # 0x2 to keep the emulated devices below the PXB bridges at 0x18+.
        pinning = PcieRootPinning(profile.uses_guest_numa)

        cmd = build_base_cmd(
            profile,
            process_name=process.name,
            firmware=firmware,
            img_path=img_path,
            foreground=process.foreground,
            pidfile=process.pidfile,
            logfile=process.logfile,
            host_nodes=host_nodes,
            kernel_path=boot.kernel,
            initrd_path=boot.initrd,
            cmdline=boot.cmdline,
            pci_pinning=pinning,
        )
        build_network(
            cmd,
            network_type=net.network_type,
            net_iface=net.net_iface,
            ssh_port=net.ssh_port,
            net_queues=net.net_queues,
            pci_pinning=pinning,
        )
        add_volumes(
            cmd,
            config_volume=volumes.config,
            cache_volume=volumes.cache,
            storage_volume=volumes.storage,
            pci_pinning=pinning,
        )
        add_vsock(cmd, pci_pinning=pinning)
        if passthrough.gpus or passthrough.nvswitches or passthrough.ib:
            # Derived, not passed: every endpoint build_pci_topology emits carries
            # `iommufd=iommufd0`, so a command with passthrough devices and no such object is
            # one QEMU refuses. The two are one decision and cannot be set apart.
            cmd.objects.append(f"iommufd,id={IOMMUFD_ID}")
        build_pci_topology(
            cmd,
            gpus=passthrough.gpus,
            nvswitches=passthrough.nvswitches,
            ib_devices=passthrough.ib,
            guest_numa=profile.uses_guest_numa,
        )
        return cmd

    @classmethod
    def for_measurement(
        cls,
        profile: HostProfile,
        *,
        firmware: str,
    ) -> "QemuCommand":
        """The same command, built for offline generation rather than a launch.

        Native throughout -- real BDFs, the launch form of ``-cpu`` -- because this IS the command
        a launch would run; ``ImageConfig`` makes its substitutions afterwards, so nothing offline
        leaks in here.

        EVERY value that differs from a launch is spelled out here, and ``create`` defaults none of
        them. That is the point: the measurement shape lives in exactly one place, so a launch
        cannot inherit a piece of it by forgetting an argument, and a reader can diff the two
        callers to see the whole divergence.

        - a synthetic node pair, so any box can generate for a guest-NUMA class
        - the canonical volume and image filenames, whose pcie.0 slots the DSDT records
        - placeholder boot artifacts: RTMR0 is boot-method independent and the measured tables
          carry no kernel
        - the tap shape with no interface: the NIC occupies a measured slot, the netdev backing
          it is not a PCI device and is not measured
        - a process that does not exist, which ``ImageConfig.to_dict()`` drops entirely
        """
        return cls.create(
            profile,
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
            "-object",
            self.tee_object,
        ]
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
        if self.foreground:
            args += ["-nographic", "-serial", "mon:stdio"]
        else:
            args += [
                "-nographic",
                "-serial",
                f"file:{self.logfile}",
                "-daemonize",
                "-pidfile",
                self.pidfile,
            ]
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


def build_base_cmd(
    profile: HostProfile,
    *,
    process_name: str,
    firmware: str,
    img_path: str,
    foreground: bool,
    pidfile: str,
    logfile: str,
    host_nodes: list[int],
    kernel_path: str,
    initrd_path: str,
    cmdline: str,
    pci_pinning: PcieRootPinning,
) -> QemuCommand:
    """Build the base QEMU command (confidential guest, firmware, CPU, memory, direct boot).

    Pure: reads no live hardware. ``host_nodes`` is the explicit guest-NUMA node
    list, fully resolved by the caller — the launcher from sysfs
    (``host_numa_nodes()`` gated by ``use_numa_topology``), the measurement
    adapter from a topology fingerprint. A guest-NUMA topology is built when it
    names >= 2 nodes; ``[]`` builds a flat guest.

    Direct boot (1.4.0+, not optional): OVMF boots ``kernel_path`` / ``initrd_path``
    with ``cmdline`` directly — no GRUB. The qcow2 stays attached as the LUKS root
    but is not the boot device (no ``bootindex``). There is deliberately no GRUB
    fallback: a second boot path would produce a second, network-inconsistent set
    of measurements. The launcher passes the artifacts published with the image;
    the offline ACPI-dump path passes placeholders (RTMR0 is boot-method
    independent and the measured tables don't include the kernel).
    """
    # The profile is the single authority on guest shape: memory, -smp, -cpu, the
    # platform, and whether this class runs a NUMA guest. Deriving NUMA from
    # len(host_nodes) instead meant two sources of truth -- a host whose live sysfs
    # disagreed with its captured profile got PXB bridges (pinned from the profile) with
    # flat memory args (derived from sysfs), a command matching no measurement.
    numa_enabled = profile.uses_guest_numa
    tee = profile.tee_provider
    mem = profile.mem
    smp_topology = profile.smp_topology

    # host_nodes stays a parameter because it genuinely differs per caller: a launch
    # binds to this machine's real nodes, while offline generation uses a synthetic pair
    # so any box can produce the measurement. It must still agree with the profile.
    if numa_enabled != (len(host_nodes) >= 2):
        raise ValueError(
            f"host_nodes {host_nodes} does not match the profile's guest-NUMA setting "
            f"(uses_guest_numa={numa_enabled}). The profile decides the guest shape; a "
            "host whose live NUMA disagrees with its captured profile cannot launch a "
            "guest any measurement was generated for."
        )
    # A flat guest binds the machine to its single backend; a NUMA guest gets one
    # backend per node via _append_numa_memory and binds none here.
    machine = tee.machine(None if numa_enabled else "mem0")

    cmd = QemuCommand(
        mem=mem,
        smp_topology=smp_topology,
        cpu_args=profile.cpu_args,
        machine=machine,
        firmware=firmware,
        process_name=process_name,
        foreground=foreground,
        logfile=logfile,
        pidfile=pidfile,
        # Pinned SMBIOS identity so per-server motherboard differences don't
        # shift RTMR0 within a profile. Single source of truth: the offline
        # measurement path reads this same builder (HostProfile.qemu_command →
        # image_config), so launch and measurement can't diverge.
        tee_object=tee.guest_object(),
        smbios=[
            f"type=1,manufacturer=Chutes,product={tee.smbios_product},version=1.0,serial=0,"
            "uuid=00000000-0000-0000-0000-000000000000",
            f"type=2,manufacturer=Chutes,product={tee.smbios_product},version=1.0,serial=0",
            "type=3,manufacturer=Chutes,version=1.0,serial=0",
        ],
    )

    if numa_enabled:
        mem_mib = _parse_mem_mib(mem)
        _append_numa_memory(cmd, mem_mib, host_nodes, tee)
        print(
            f"NUMA: {len(host_nodes)} guest nodes, "
            f"{mem_mib // len(host_nodes)}M each (approx), host nodes {host_nodes}"
        )
    else:
        cmd.objects.append(tee.memory_backend("mem0", mem))

    # Direct boot (always): OVMF loads the kernel/initrd/cmdline itself. The qcow2
    # is still the LUKS root, just not the boot device — so no bootindex.
    cmd.kernel = kernel_path
    cmd.initrd = initrd_path
    cmd.append = cmdline

    img_fmt = _block_format(img_path)
    drive_opts = f"file={img_path},if=none,id=virtio-disk0,cache=none,aio=native,format={img_fmt}"
    if img_fmt == "qcow2":
        drive_opts += ",discard=unmap"
    elif img_fmt == "raw":
        drive_opts += ",discard=on,detect-zeroes=on"
    cmd.drives.append(drive_opts)
    dev_opts = f"virtio-blk-pci,drive=virtio-disk0{pci_pinning.device_suffix()}"
    if img_fmt == "raw":
        dev_opts += ",num-queues=4"
    cmd.devices.append(dev_opts)

    return cmd


def build_network(
    cmd: QemuCommand,
    *,
    network_type: str,
    net_iface: str | None,
    ssh_port: int,
    net_queues: int = 4,
    pci_pinning: PcieRootPinning,
):
    """Add the guest NIC to the QemuCommand.

    A launch always has exactly one NIC, so the device is unconditional; the ``-netdev`` that
    backs it follows the inputs. In tap mode that needs a host interface -- without one the
    device is emitted alone, which is what offline measurement generation wants: the device
    occupies a pcie.0 slot and slot layout is measured into RTMR0, while a netdev is not a PCI
    device and is not measured.

    Whether an interface SHOULD have been supplied is the caller's question, not this one.
    """
    if network_type == "tap":
        vectors = 2 * net_queues + 2
        cmd.devices.append(
            f"virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56,mq=on,vectors={vectors},mrg_rxbuf=on"
            f"{pci_pinning.device_suffix()}"
        )
        if net_iface:
            print(
                f"Networking: TAP mode (iface={net_iface}, queues={net_queues}, vhost=on)"
            )
            cmd.netdevs.append(
                f"tap,id=n0,ifname={net_iface},script=no,downscript=no,"
                f"vhost=on,queues={net_queues}"
            )
    else:
        print("Networking: Canonical user-mode networking")
        cmd.devices.append(
            f"virtio-net-pci,netdev=nic0_td{pci_pinning.device_suffix()}"
        )
        cmd.netdevs.append(f"user,id=nic0_td,hostfwd=tcp::{ssh_port}-:22")


def add_volumes(
    cmd: QemuCommand,
    *,
    config_volume: str | None,
    cache_volume: str | None,
    storage_volume: str | None,
    pci_pinning: PcieRootPinning,
):
    """Add config, cache, and storage volumes to the QemuCommand."""
    if config_volume:
        cmd.drives.append(
            f"file={config_volume},if=none,id=virtio-config,cache=none,format=qcow2,readonly=on"
        )
        cmd.devices.append(
            f"virtio-blk-pci,drive=virtio-config{pci_pinning.device_suffix()}"
        )
    for vol_path, vol_id in [
        (cache_volume, "virtio-cache"),
        (storage_volume, "virtio-storage"),
    ]:
        if not vol_path:
            continue
        vol_fmt = _block_format(vol_path)
        drive_opts = f"file={vol_path},if=none,id={vol_id},cache=none,aio=native,format={vol_fmt}"
        if vol_fmt == "raw":
            drive_opts += ",discard=on,detect-zeroes=on"
        cmd.drives.append(drive_opts)
        dev_opts = f"virtio-blk-pci,drive={vol_id}{pci_pinning.device_suffix()}"
        if vol_fmt == "raw":
            dev_opts += ",num-queues=4"
        cmd.devices.append(dev_opts)


def add_vsock(cmd: QemuCommand, *, pci_pinning: PcieRootPinning):
    """Add vhost-vsock device to the QemuCommand."""
    cmd.devices.append(f"vhost-vsock-pci,guest-cid=3{pci_pinning.device_suffix()}")
