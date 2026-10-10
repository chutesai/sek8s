"""Everything one guest needs to build its QEMU command, and what each kind of guest varies.

``HostProfile`` is what this machine IS. A ``GuestContext`` is what one guest NEEDS: its image,
firmware, boot artifacts, network, volumes, devices and process -- and, because the environment a
guest runs in follows from what kind of guest it is, the handful of command arguments that differ
between a real launch and the offline measurement dump. ``QemuCommand.build(host, context)`` walks
one fixed traversal and asks the context for each of those; it never learns which kind it holds.

    GuestContext                 the data, and the eight environment leaves
    ├── LaunchContext            a real guest on the TEE host: the leaves call host.tee_provider
    │   ├── TdxLaunchContext
    │   └── SnpLaunchContext
    └── MeasurementContext       the offline dump: no TEE, no GPUs, less RAM than the guest

The launch splits by platform only where its data differs; the platform's static QEMU arguments
come from ``host.tee_provider`` inside the leaves.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from chutes_cvm.guest.devices import PciDevice
from chutes_cvm.guest.direct_boot import direct_boot_artifacts
from chutes_cvm.guest.host_class import MeasuredImage, SnpMeasuredImage
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.tee import (
    Q35_MACHINE,
    SnpTeeProvider,
    TdxTeeProvider,
    TeeProvider,
)
from chutes_cvm.paths import firmware_path

#: The iommufd object id every vfio-pci endpoint references. Spelled once: an endpoint naming an
#: id no object declares is a command QEMU refuses, and it used to be written out in three places.
IOMMUFD_ID = "iommufd0"


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


@dataclass(frozen=True)
class QemuDevice:
    """One emulated device as the command carries it: the ``-device`` argument at its slot, and
    the ``-drive`` backing it, if the context attaches one."""

    device: str
    drive: "str | None" = None


@dataclass(frozen=True)
class GuestContext(ABC):
    """One guest, as its command is built from it."""

    #: The root disk: the per-VM qcow2 copy on a launch, a placeholder name offline.
    image: str
    firmware: str
    #: Host NUMA nodes the guest's memory binds to, one guest node each; empty for a flat guest.
    host_nodes: tuple[int, ...]
    boot: DirectBoot
    network: GuestNetwork
    volumes: GuestVolumes
    process: ProcessBundle
    passthrough: PassthroughSet

    @property
    def kernel_cmdline(self) -> str:
        """The ``-append`` this guest boots: the image's own cmdline."""
        return self.boot.cmdline

    # ── the environment leaves: what differs between a launch and the offline dump ─────────
    @abstractmethod
    def machine(self, host: HostProfile, flat_backend: "str | None") -> str:
        """The ``-machine`` argument. ``flat_backend`` names the single memory backend a flat
        guest binds, or is None under guest NUMA where per-node memdevs bind instead."""

    @abstractmethod
    def memory_backend(
        self,
        host: HostProfile,
        backend_id: str,
        size: str,
        host_node: "int | None" = None,
    ) -> str:
        """One ``-object`` memory backend."""

    @abstractmethod
    def cpu_args(self, host: HostProfile) -> str:
        """The ``-cpu`` string."""

    @abstractmethod
    def tee_object(self, host: HostProfile) -> "str | None":
        """The confidential-guest ``-object``, or None for a command that declares none."""

    @abstractmethod
    def serial(self) -> list[str]:
        """The ``-serial`` values."""

    @abstractmethod
    def qemu_device(
        self, device: str, slot: str, drive: "str | None" = None, opts: str = ""
    ) -> QemuDevice:
        """One emulated device at ``slot``, with its backing drive if this context keeps one.

        ``opts`` are device options that must follow the address. QEMU does not care -- device
        options are comma-separated and order-independent -- but it is the order every published
        measurement was generated against, so it is preserved rather than tidied.
        """

    @abstractmethod
    def endpoint(self, host: HostProfile, device: PciDevice, rp_id: str) -> str:
        """The ``-device`` argument for one passthrough endpoint on ``rp_id``."""

    @abstractmethod
    def wants_iommufd(self) -> bool:
        """Whether the endpoints this context emits reference an iommufd object."""


@dataclass(frozen=True)
class LaunchContext(GuestContext):
    """A real guest on this TEE host. Every leaf comes from the host's ``TeeProvider`` or the
    host profile itself; nothing here knows about measurement."""

    #: Whether to bind and attach this host's GPUs.
    pass_gpus: bool = False
    #: Print the SSH login hint after launch (benchmark and debug guests).
    show_ssh: bool = False

    #: The platform this launch runs on.
    provider_type: ClassVar[type[TeeProvider]]

    @classmethod
    def type_for(cls, host: HostProfile) -> "type[LaunchContext]":
        """The launch context type for ``host``'s platform."""
        for context_type in (TdxLaunchContext, SnpLaunchContext):
            if isinstance(host.tee_provider, context_type.provider_type):
                return context_type
        raise ValueError(f"no LaunchContext for {host.tee_provider.label}")

    @classmethod
    def from_host(
        cls,
        host: HostProfile,
        measured: "MeasuredImage | None",
        *,
        image: str,
        volumes: GuestVolumes,
        network: GuestNetwork,
        process: ProcessBundle,
        pass_gpus: bool,
        show_ssh: bool = False,
    ) -> "LaunchContext":
        """This host's launch of ``image``, the per-VM copy of an image set.

        ``measured`` is the image's entry in this host's class, or None for a test boot (a debug
        build, ``--force``, or a benchmark), which boots but cannot attest. Only SEV-SNP's guest
        differs between the two.

        Reads the boot artifacts staged beside ``image`` and, for a NUMA guest, the host's online
        nodes. Firmware is a property of the platform, not the GPU profile: TDX boots the pinned
        TDVF, SNP the AMD OVMF build. ``--no-gpus`` leaves the GPUs bound to their host driver, so
        a command that named them would be one QEMU refuses.
        """
        kernel, initrd, cmdline = direct_boot_artifacts(image)
        return cls.type_for(host)._create(
            measured,
            image=image,
            firmware=firmware_path(host.tee_provider.default_firmware),
            host_nodes=tuple(host_numa_nodes()) if host.uses_guest_numa else (),
            boot=DirectBoot(kernel, initrd, cmdline),
            network=network,
            volumes=volumes,
            process=process,
            passthrough=(
                PassthroughSet.from_profile(host) if pass_gpus else PassthroughSet()
            ),
            pass_gpus=pass_gpus,
            show_ssh=show_ssh,
        )

    @classmethod
    def _create(cls, measured: "MeasuredImage | None", **fields) -> "LaunchContext":
        """This platform's guest. Intel TDX takes nothing from ``measured``: ACPI is attested
        through RTMR0, so a measured launch and a test boot are the same guest."""
        return cls(**fields)

    def machine(self, host: HostProfile, flat_backend: "str | None") -> str:
        return host.tee_provider.machine(flat_backend)

    def memory_backend(
        self,
        host: HostProfile,
        backend_id: str,
        size: str,
        host_node: "int | None" = None,
    ) -> str:
        return host.tee_provider.memory_backend(backend_id, size, host_node=host_node)

    def cpu_args(self, host: HostProfile) -> str:
        return host.cpu_args

    def tee_object(self, host: HostProfile) -> "str | None":
        return host.guest_object()

    def serial(self) -> list[str]:
        if self.process.foreground:
            return ["mon:stdio"]
        return [f"file:{self.process.logfile}"]

    def qemu_device(
        self, device: str, slot: str, drive: "str | None" = None, opts: str = ""
    ) -> QemuDevice:
        return QemuDevice(device=f"{device}{slot}{opts}", drive=drive)

    def endpoint(self, host: HostProfile, device: PciDevice, rp_id: str) -> str:
        return f"vfio-pci,host={device.bdf},bus={rp_id},addr=0x0,iommufd={IOMMUFD_ID}"

    def wants_iommufd(self) -> bool:
        return True


@dataclass(frozen=True)
class TdxLaunchContext(LaunchContext):
    """An Intel TDX launch: nothing beyond the launch itself."""

    provider_type = TdxTeeProvider


@dataclass(frozen=True)
class SnpLaunchContext(LaunchContext):
    """An AMD SEV-SNP launch. Its firmware checks the guest's ACPI tables against
    ``sek8s.acpi_sha256`` on the kernel cmdline: the hash published for this class, or
    ``unverified`` on a test boot."""

    #: The ACPI hash the firmware checks, or ``ACPI_UNVERIFIED``.
    acpi_sha256: str = field(kw_only=True)

    provider_type = SnpTeeProvider
    ACPI_CMDLINE_PARAM: ClassVar[str] = "sek8s.acpi_sha256"
    #: The test-boot value: the firmware boots without checking, and the guest cannot attest.
    ACPI_UNVERIFIED: ClassVar[str] = "unverified"

    @property
    def kernel_cmdline(self) -> str:
        return self.with_acpi(self.boot.cmdline, self.acpi_sha256)

    @classmethod
    def with_acpi(cls, cmdline: str, acpi_sha256: str) -> str:
        """``cmdline`` carrying the ACPI value an SEV-SNP firmware checks. The one place it is
        composed -- the SEV-SNP measurement digests this same string."""
        param = f"{cls.ACPI_CMDLINE_PARAM}={acpi_sha256}"
        return f"{cmdline} {param}" if cmdline else param

    @classmethod
    def _create(cls, measured: "MeasuredImage | None", **fields) -> "LaunchContext":
        if measured is None:
            return cls(**fields, acpi_sha256=cls.ACPI_UNVERIFIED)
        if not isinstance(measured, SnpMeasuredImage):
            raise TypeError(
                f"an SEV-SNP launch needs an SnpMeasuredImage, got {measured!r}"
            )
        return cls(**fields, acpi_sha256=measured.acpi_sha256)


@dataclass(frozen=True)
class MeasurementContext(GuestContext):
    """The guest offline generation dumps ACPI from, built for that purpose rather than rewritten.

    Every leaf answers "what reproduces the launch's measured ACPI on a box with no TEE, no GPUs and
    less RAM than the guest". The same traversal builds both commands, so a device added to the
    launch gets its slot here too; only what fills each slot differs.
    """

    @classmethod
    def from_host(cls, host: HostProfile, *, firmware: str) -> "MeasurementContext":
        """The dump guest for ``host``'s class: its passthrough set and guest-NUMA shape from the
        profile, placeholders for everything that is not measured."""
        return cls(
            image="root.qcow2",
            firmware=firmware,
            host_nodes=(0, 1) if host.uses_guest_numa else (),
            boot=DirectBoot(kernel="/dev/null", initrd="/dev/null", cmdline=""),
            network=GuestNetwork(network_type="tap", net_iface=None, ssh_port=0),
            volumes=GuestVolumes(
                config="config.qcow2", cache="cache.raw", storage="storage.raw"
            ),
            process=ProcessBundle(name="chutes-measure"),
            passthrough=PassthroughSet.from_profile(host),
        )

    def machine(self, host: HostProfile, flat_backend: "str | None") -> str:
        # The launch machine without the confidential-guest object (the dumper's QEMU has none),
        # with what the platform switches off implicitly spelled out, so the dumped tables match
        # the launch's byte for byte. A flat topology wires guest RAM to a machine-level backend;
        # without it QEMU falls back to allocating the full pc.ram, which a small generating host
        # cannot back (the mem0 object carries reserve=off).
        tee = host.tee_provider
        parts = [Q35_MACHINE, *tee.machine_opts, *tee.implicit_machine_opts]
        if flat_backend:
            parts.append(f"memory-backend={flat_backend}")
        return ",".join(parts)

    def memory_backend(
        self,
        host: HostProfile,
        backend_id: str,
        size: str,
        host_node: "int | None" = None,
    ) -> str:
        """The launch's backend type, unbound and unreserved.

        ``reserve=off`` maps any-size guest RAM on a small host without allocating it, and the
        host-NUMA binding is dropped because the generating box's nodes are not the launch host's.
        """
        provider = host.tee_provider
        parts = [provider.memory_backend_type, f"id={backend_id}", f"size={size}"]
        parts.extend(provider.memory_backend_opts)
        parts.append("reserve=off")
        return ",".join(parts)

    def cpu_args(self, host: HostProfile) -> str:
        """The launch ``-cpu`` plus an explicit CPU identity.

        ``vendor`` fixes the SRAT memory hole (AMD-guest-gated); the SMBIOS Type-4 Processor ID is
        patched separately by tdx-measure. BOTH must be set, so a host with neither captured is
        refused rather than measured as the generating host's CPU.
        """
        cpu = host.cpu
        if not cpu.vendor or cpu.processor_id is None:
            raise ValueError(
                f"host class {host.variant_label!r} has no captured CPU model "
                "(processor_id is None); offline RTMR0 would be generated for the generating "
                "host's CPU. Re-register from a host of this class with a current chutes-cvm."
            )
        return f"{host.cpu_args},vendor={cpu.vendor}"

    def tee_object(self, host: HostProfile) -> "str | None":
        return None

    def serial(self) -> list[str]:
        return ["null"]  # adds COM1 to the DSDT

    def qemu_device(
        self, device: str, slot: str, drive: "str | None" = None, opts: str = ""
    ) -> QemuDevice:
        """A backing-free filler at the slot this device was given.

        The dump has no drives or netdevs to reference, but the slot lands in the DSDT and so in
        RTMR0 -- so the slot is kept and only the backing goes.
        """
        return QemuDevice(device=f"virtio-rng-pci{slot}")

    def endpoint(self, host: HostProfile, device: PciDevice, rp_id: str) -> str:
        """A ``pci-bar-stub`` carrying this device's own BAR layout.

        The stub reproduces the MMIO windows the real BARs would create, which is what OVMF sizes
        the guest's 64-bit aperture from. Per-device rather than one representative per kind, so a
        GPU model is measured from a submitted profile rather than a transcribed table.
        """
        if not device.bars:
            raise ValueError(
                f"no BARs captured for {rp_id!r} on a "
                f"{host.gpu_profile.name!r} host. The stub reproduces the guest's MMIO "
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
