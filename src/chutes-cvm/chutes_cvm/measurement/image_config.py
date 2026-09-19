"""The ``ImageConfig`` handed to tdx-measure, built from a QemuCommand.

``HostProfile.qemu_command`` yields the QEMU command a launch would run. The
offline path needs a slightly different one that yields the **same measured ACPI**
without hardware. ``ImageConfig`` is a view over that ``QemuCommand`` reading its
fields directly — no command re-parsing, so it cannot drift from the builder — and
applies the substitutions the fork needs:

  - **machine**: run plain q35 (``smm=off,pic=off``); drop the tdx-guest object
    (not carried over — the dumper QEMU has no confidential-guest support).
  - **memory**: ``reserve=off`` on every backend (maps any-size guest RAM on a
    small host without allocating it) and strip host-nodes/policy binding.
  - **emulated devices**: replace the boot disk with backing-free slot-fillers so
    pcie.0 slots 0x2-0x7 populate the DSDT without real drives.
  - **passthrough**: swap each ``vfio-pci`` endpoint for a ``pci-bar-stub``
    carrying that device's own captured BAR layout, reproducing the MMIO windows
    the real BARs would create.
  - **serial**: attach one so COM1 appears in the DSDT.
  - **cpu**: pin the guest's CPU identity (``vendor=``) so a generating box reproduces the
    production guest's CPU rather than its own.

tdx-measure does the dumping itself inside its container; this only produces its
input. Reproduces a real launch's measured ``etc/acpi/tables`` byte-for-byte with no
GPU present (validated against box-028).
"""

import re
from dataclasses import dataclass

from chutes_cvm.guest.devices import PciBar, PciDevice
from chutes_cvm.guest.gpu.profiles import PassthroughDevice
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.qemu import QemuCommand

# The dumper runs plain q35 (no TDX): the ACPI tables are identical, and the
# container QEMU has no confidential-guest support.
_DUMP_MACHINE = "q35,kernel_irqchip=split,smm=off,pic=off"

# A launch places six emulated devices on pcie.0 (boot disk, net, 3 volumes, vsock) with no
# explicit addr=, so QEMU auto-assigns them the lowest free slots. Which slots those are depends
# on the topology: with PXB bridges present (guest-NUMA) they land at 0x2-0x7, without them (flat)
# one slot lower, at 0x1-0x6. Verified against live DSDTs from both paths on one host:
#   NUMA  _ADR slots [2,3,4,5,6,7, 24,25, 31]    FLAT  _ADR slots [1,2,3,4,5,6, 8,9, 31]
# Their DSDT nodes are slot-populated markers only (device-type agnostic), so backing-free
# fillers reproduce them -- but only at the right slots, or every device node shifts and the
# DSDT digest (RTMR0 ACPI event) changes.
#: How many emulated devices a standard launch puts on pcie.0, auto-assigned (no addr=):
#: boot disk, net, config volume, cache volume, storage volume, vsock. NOT derivable from the
#: command the generator builds -- HostProfile.qemu_command emits only the boot disk; the rest
#: are added later by the launch orchestrator (build_network, volume setup), which the generator
#: never runs. Changing the volume set changes this number, and a benchmark launch (no cache
#: volume) is already a different shape.
_EMULATED_DEVICE_COUNT = 6


def _emulated_slots(devices: list[str]) -> range:
    """The pcie.0 slots QEMU auto-assigns to the launch's emulated devices.

    The first slot depends on whether PXB bridges are present, and only on that: measured with
    2, 3 and 4 bridges the emulated devices start at 0x2 in every case, and at 0x1 with none.
    So a future guest-NUMA topology with more nodes stays correct here.
    """
    first = 0x2 if any(d.startswith("pxb-pcie") for d in devices) else 0x1
    return range(first, first + _EMULATED_DEVICE_COUNT)


def _bars_arg(bars: list[PciBar]) -> str:
    """Format a BAR layout as the pci-bar-stub ``bars=`` value (``;``-separated)."""
    return ";".join(b.as_stub_arg() for b in bars)


def _reserve_off(backend: str) -> str:
    """Strip host-NUMA binding and add reserve=off to a memory-backend object."""
    backend = re.sub(r",host-nodes=\d+", "", backend)
    backend = backend.replace(",policy=bind", "")
    if "reserve=" not in backend:
        backend += ",reserve=off"
    return backend


@dataclass
class ImageConfig:
    """The ``ImageConfig`` tdx-measure consumes to reproduce a launch's ACPI.

    Build from the host's own ``QemuCommand`` + its ``HostProfile``; ``to_dict()``
    is the metadata JSON. Reads the shared ``QemuCommand``'s structured fields —
    no re-parsing — so it stays tied to the real launch command.
    """

    cmd: QemuCommand
    host: HostProfile
    acpi_tables: str
    with_smbios: bool = True

    @property
    def cpu_args(self) -> str:
        """The launch ``-cpu`` plus an explicit CPU identity.

        ``vendor`` fixes the SRAT memory hole (AMD-guest-gated); the SMBIOS Type-4 Processor ID
        is patched separately by tdx-measure from ``processor_id`` -- so BOTH must be set, and a
        host with neither captured is refused rather than measured as the generating host's CPU.
        """
        if not self.host.cpu.vendor or self.host.cpu.processor_id is None:
            raise ValueError(
                f"host class {self.host.variant_label!r} has no captured CPU model "
                f"(processor_id is None); offline RTMR0 would be generated for the generating "
                f"host's CPU. Re-register from a host of this class with a current chutes-cvm."
            )
        return f"{self.cmd.cpu_args},vendor={self.host.cpu.vendor}"

    @property
    def objects(self) -> list[str]:
        # Only the memory-backends (cmd.objects); the tdx-guest object lives in
        # cmd.tdx_guest and is simply not carried over.
        return [_reserve_off(o) for o in self.cmd.objects]

    @property
    def devices(self) -> list[str]:
        """Fillers for the emulated slots, then the passthrough topology with stubbed BARs."""
        out = [
            f"virtio-rng-pci,bus=pcie.0,addr={s:#x}"
            for s in _emulated_slots(self.cmd.devices)
        ]
        for dev in self.cmd.devices:
            if dev.startswith("virtio-blk-pci,drive=virtio-disk0"):
                continue  # boot disk — replaced by the slot-fillers above
            if dev.startswith("vfio-pci"):
                out.append(self._swap_endpoint(dev))
            else:
                out.append(dev)  # pxb-pcie / pcie-root-port
        return out

    def _endpoint_for(self, root_port: str) -> PassthroughDevice:
        """The captured device behind a root port, or the profile's fallback entry.

        ``root_port`` is the QEMU ``pcie-root-port`` id the endpoint hangs off, as it appears in
        the device's ``bus=`` -- ``rp3`` for the third GPU, ``rp_nvsw1``, ``rp_ib1``.

        Root ports are emitted in device order, so ``rp3`` is the third GPU. Using that device's
        own geometry rather than one representative per kind is what lets a GPU model be measured
        from a submitted profile instead of a hand-transcribed table entry -- and it is correct
        even if two devices ever differ, since OVMF sizes the aperture from what it enumerates.
        """
        devices: tuple[PciDevice, ...]
        if ordinal := re.fullmatch(r"rp(\d+)", root_port):
            kind, devices = "gpu", self.host.gpus
        elif ordinal := re.fullmatch(r"rp_nvsw(\d+)", root_port):
            kind, devices = "nvswitch", self.host.attached_nvswitches
        elif ordinal := re.fullmatch(r"rp_ib(\d+)", root_port):
            kind, devices = "ib", self.host.attached_ib
        else:
            raise NotImplementedError(f"unrecognized passthrough bus {root_port!r}")

        index = int(ordinal.group(1)) - 1
        if index < len(devices) and devices[index].bars:
            device = devices[index]
            return PassthroughDevice(
                vendor=int(device.vendor, 16),
                device_id=device.device_id,
                pci_class=int(device.pci_class, 16),
                bars=list(device.bars),
            )

        raise ValueError(
            f"no BARs captured for {root_port!r} ({kind}) on a "
            f"{self.host.gpu_profile.name!r} host. The stub reproduces the guest's MMIO windows "
            f"from them, so without them the generated RTMR0 matches no real boot. Re-submit "
            f"this host's profile with a current chutes-cvm."
        )

    def _swap_endpoint(self, device_arg: str) -> str:
        """Swap a ``vfio-pci`` endpoint for a ``pci-bar-stub`` carrying that device's BARs.

        ``device_arg`` is the endpoint's whole ``-device`` argument, e.g.
        ``vfio-pci,host=0000:1b:00.0,bus=rp1``.
        """
        bus = re.search(r"bus=([^,]+)", device_arg)
        if not bus:
            raise ValueError(f"vfio-pci device without a bus=: {device_arg!r}")
        root_port = bus.group(1)
        spec = self._endpoint_for(root_port)
        return (
            f"pci-bar-stub,bus={root_port},bars={_bars_arg(spec.bars)},"
            f"vendor={spec.vendor:#06x},device={int(spec.device_id, 16):#06x},"
            f"class={spec.pci_class:#06x}"
        )

    @property
    def smbios(self) -> list[str]:
        return self.cmd.smbios if self.with_smbios else []

    def to_dict(self) -> dict:
        cmd = self.cmd
        # Flat topologies wire the guest RAM to a machine-level memory-backend
        # (`memory-backend=mem0`); NUMA wires per-node memdevs (`-numa … memdev=`)
        # instead. The dump machine must keep whichever the launch used — without the
        # flat memory-backend, QEMU falls back to allocating the full pc.ram, which a
        # small generating host can't back (the mem0 object carries reserve=off).
        dump_machine = _DUMP_MACHINE
        mem_backend = next(
            (p for p in cmd.machine.split(",") if p.startswith("memory-backend=")),
            None,
        )
        if mem_backend:
            dump_machine = f"{_DUMP_MACHINE},{mem_backend}"
        return {
            "boot_config": {
                "cpus": int(cmd.smp_topology.split(",", 1)[0]),
                "memory": cmd.mem,
                "bios": cmd.firmware,
                "acpi_tables": self.acpi_tables,
                "qemu": {
                    "machine": dump_machine,
                    "cpu": self.cpu_args,
                    "accel": cmd.accel,
                    "smp": cmd.smp_topology,
                    "objects": self.objects,
                    "numa": cmd.numa,
                    "smbios": self.smbios,
                    "serial": ["null"],  # adds COM1 to the DSDT
                    "devices": self.devices,
                    "fw_cfg": cmd.fw_cfg,
                    # Pin the SMBIOS Type-4 Processor ID (#14) to the production
                    # CPUID; tdx-measure patches it into the dumped SMBIOS (KVM
                    # can't override the generating host's CPUID). None => unpatched.
                    "processor_id": self.host.cpu.processor_id,
                },
            },
            "direct": {"kernel": "/dev/null", "initrd": "/dev/null", "cmdline": ""},
        }
