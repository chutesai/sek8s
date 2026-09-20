"""PCI devices as the host reports them.

One object per physical device, carrying everything ``lspci`` and sysfs can tell us about it.
This is the observed half of a host profile; ``GpuProfile`` is the authored half (how we choose
to run a GPU model). They join on ``GpuDevice.device_id == GpuProfile.pci_device_id``.

What belongs here is decided by one test: **can lspci or sysfs read it with the GPU bound to
vfio-pci?** On a passthrough-prepped host the NVIDIA driver is detached, so ``nvidia-smi`` sees
nothing -- every stored profile we have carries ``vram_gb: null`` and ``vbios: ["", ...]`` for
exactly that reason. BARs pass the test (``/sys/bus/pci/devices/*/resource`` is world-readable
and driver-independent); VRAM and VBIOS fail it and stay on ``GpuProfile``.

BAR geometry is measurement-critical: perturbing a GPU BAR's ``size_mb`` or ``kind`` moves RTMR0,
and so does an NVSwitch BAR's size, so every passed-through device carries its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Self


@dataclass(frozen=True)
class PciBar:
    """One PCI Base Address Register, from an ``lspci -vvv`` ``Region N:`` line.

    ``kind`` is ``m32``/``m64``/``p32``/``p64`` -- (m)em non-prefetchable / (p)refetchable, 32- or
    64-bit addressing. A 64-bit BAR consumes two slots, so a card with three of them reports at
    indices 0/2/4, not 0/1/2.

    Measured against a real H200: ``size_mb`` moves RTMR0, and so does ``kind`` -- prefetchability
    and addressing width each independently. A uniform index shift did not move it, which is the
    least disturbing shift possible, so treat ``index`` as unproven rather than inert.

    Never exists apart from its device; ``PciDevice`` composes it.
    """

    index: int
    size_mb: int
    kind: str

    def as_stub_arg(self) -> str:
        """This BAR as QEMU's ``pci-bar-stub`` syntax: ``index:size:kind``."""
        size = (
            f"{self.size_mb // 1024}G"
            if self.size_mb % 1024 == 0
            else f"{self.size_mb}M"
        )
        return f"{self.index}:{size}:{self.kind}"


@dataclass(frozen=True)
class PciDevice:
    """A PCI device as the host reports it -- identity, NUMA affinity, BAR layout.

    ``numa_node`` is -1 where sysfs reports no affinity; it is kept rather than dropped because
    the per-device node vector drives the guest's PXB-PCIe grouping and therefore RTMR0.
    """

    bdf: str
    vendor: str
    device_id: str
    pci_class: str
    numa_node: int
    bars: tuple[PciBar, ...] = field(default_factory=tuple)

    def __lt__(self, other: "PciDevice") -> bool:
        """Devices order by BDF.

        Not cosmetic: the per-device NUMA vector is read in this order, and it drives the guest's
        PXB-PCIe grouping and root-port numbering. A permutation is a different machine.
        """
        return self.bdf < other.bdf

    @property
    def bars_arg(self) -> str:
        """The device's BAR layout as QEMU's ``bars=`` value (``;``-separated)."""
        return ";".join(
            b.as_stub_arg() for b in sorted(self.bars, key=lambda b: b.index)
        )

    @classmethod
    def from_dicts(cls, raw: "list[dict] | None") -> tuple[Self, ...]:
        """Every device of this kind, in BDF order.

        A tuple, not a list: the devices are frozen and the ordering is an invariant, so the
        collection should not invite an append or a re-sort either.

        Sorted here so the ordering is an invariant of the list rather than something each reader
        re-applies -- miss it once and the NUMA vector describes a machine that does not exist.
        """
        return tuple(sorted(cls.from_dict(d) for d in (raw or ())))

    @classmethod
    def keys(cls) -> tuple[str, ...]:
        """The document keys this device must carry: its own fields.

        Derived rather than listed, so adding a field cannot leave a document that omits it
        validating. Subclasses need no override -- ``fields`` already includes what they add.
        """
        return tuple(f.name for f in fields(cls))

    @classmethod
    def _kwargs_from(cls, d: dict) -> dict:
        """Constructor kwargs for one device document, or raise naming what is absent.

        Every key is required. Defaulting a missing one would turn a malformed
        document into a device with no identity, which surfaces much later as a stub carrying the
        wrong MMIO windows. An absent key is a broken capture and says so here.

        Empty *values* are a different matter and are allowed: ``numa_node`` is -1 where sysfs
        reports no affinity, and ``bars`` is empty where lspci found no sized regions. Whether
        either is acceptable depends on the device actually being attached, which this layer does
        not know -- that check belongs where the consequence is known.
        """
        missing = [k for k in cls.keys() if k not in d]
        if missing:
            raise ValueError(f"device document missing {', '.join(missing)}: {d!r}")
        return {
            "bdf": str(d["bdf"]),
            "vendor": str(d["vendor"]).lower(),
            "device_id": str(d["device_id"]).lower(),
            "pci_class": str(d["pci_class"]),
            "numa_node": int(d["numa_node"]),
            "bars": tuple(
                PciBar(int(b["index"]), int(b["size_mb"]), str(b["kind"]))
                for b in d["bars"]
            ),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Self:
        return cls(**cls._kwargs_from(d))

    def to_api_dict(self) -> dict:
        """This device as the API stores it: the fields that reach RTMR0, and nothing else.

        No ``bdf``. It is not an RTMR0 input -- the measured command substitutes every
        ``vfio-pci,host=<bdf>`` for a ``pci-bar-stub`` -- so two hosts with the same cards in
        different slots measure identically and must not land in different classes. The only thing
        it contributed to the stored shape was position, which the list already encodes.
        """
        return {
            "vendor": self.vendor,
            "device_id": self.device_id,
            "pci_class": self.pci_class,
            "numa_node": self.numa_node,
            "bars": [
                {"index": b.index, "size_mb": b.size_mb, "kind": b.kind}
                for b in self.bars
            ],
        }

    def to_dict(self) -> dict:
        return {
            "bdf": self.bdf,
            "vendor": self.vendor,
            "device_id": self.device_id,
            "pci_class": self.pci_class,
            "numa_node": self.numa_node,
            "bars": [
                {"index": b.index, "size_mb": b.size_mb, "kind": b.kind}
                for b in self.bars
            ],
        }


@dataclass(frozen=True)
class GpuDevice(PciDevice):
    """One passed-through GPU. Its ``device_id`` selects the ``GpuProfile``."""


@dataclass(frozen=True)
class NvSwitchDevice(PciDevice):
    """One NVSwitch. Attached only when the GPU profile passes NVSwitches through."""


@dataclass(frozen=True)
class IbDevice(PciDevice):
    """One InfiniBand PF.

    ``is_bridge_pf`` marks a ConnectX-7 NVSwitch bridge (VPD ``SMDL=SW_MNG``): it must stay on the
    host for Fabric Manager, never passed through. Reading VPD needs root, so an unprivileged
    capture cannot see it -- the flag is only trustworthy from a privileged discovery.
    """

    is_bridge_pf: bool = False
    is_vf: bool = False

    @classmethod
    def _kwargs_from(cls, d: dict) -> dict:
        return super()._kwargs_from(d) | {
            "is_bridge_pf": bool(d["is_bridge_pf"]),
            "is_vf": bool(d["is_vf"]),
        }

    def to_api_dict(self) -> dict:
        # Attachment already excludes bridge PFs and VFs and only attached IB is submitted, so
        # both flags would be constant; they stay on the capture side. Restated as their
        # post-gating values so the document still parses back into an IbDevice.
        return super().to_api_dict() | {"is_bridge_pf": False, "is_vf": False}

    def to_dict(self) -> dict:
        return super().to_dict() | {
            "is_bridge_pf": self.is_bridge_pf,
            "is_vf": self.is_vf,
        }


def sole_device_id(devices: tuple) -> str:
    """The one device id shared by every device, or raise.

    The document describes one passthrough endpoint per platform and the profile resolves from a
    single model, so a mixed set cannot be described. Checked here against real per-device data
    rather than assumed from one hoisted representative.
    """
    ids = {d.device_id for d in devices}
    if len(ids) != 1:
        raise ValueError(
            f"expected one GPU model, found {sorted(ids) or 'none'}; "
            "a host with mixed GPU device ids cannot be profiled"
        )
    return ids.pop()
