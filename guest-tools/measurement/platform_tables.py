"""Turn a measurement ``MachineSpec`` into tdx-measure metadata.

The shared ``build_qemu_command`` produces the *launch* command (real machine,
vfio endpoints, drive-backed emulated devices). The offline ACPI dumper needs a
slightly different command that yields the **same measured ACPI** without any
hardware. This module runs the spec through ``build_qemu_command`` and rewrites
the result — the single measurement-side place that knows how a launch command
maps to a dump command:

  - **machine**: drop ``confidential-guest-support=tdx`` and run the plain q35
    (``smm=off,pic=off``) the patched dumper QEMU expects; drop the tdx-guest
    object.
  - **memory**: ``reserve=off`` on every backend (maps any-size guest RAM on a
    small host without allocating it) and strip host-nodes/policy binding.
  - **emulated devices**: replace the boot disk with backing-free slot-fillers so
    pcie.0 slots 0x2-0x7 populate the DSDT without real drives.
  - **passthrough**: swap each ``vfio-pci`` endpoint for a ``pci-bar-stub``
    carrying the device's BAR layout (from the profile), reproducing the per-GPU
    MMIO windows the real BARs would create.
  - **serial**: attach one so COM1 appears in the DSDT.

Reproduces a real launch's measured ``etc/acpi/tables`` byte-for-byte with no GPU
present (validated against box-028). Imports the shared VM lib from
``chutes.guest``; callers must have ``host-tools/scripts`` on ``sys.path``.
"""

import re

from chutes.guest.command import MachineSpec, build_qemu_command
from chutes.guest.gpu.profiles import GpuProfile, PciBar

# NVIDIA vendor; all supported GPUs report class 0x0302 (3D controller). The
# stub impersonates this identity so the generated ACPI matches a real device.
_NVIDIA_VENDOR = 0x10DE
_GPU_CLASS = 0x0302

# The dumper runs plain q35 (no TDX): the ACPI tables are identical, and the
# container QEMU has no confidential-guest support.
_DUMP_MACHINE = "q35,kernel_irqchip=split,smm=off,pic=off"

# The emulated devices a launch places on pcie.0 (boot disk, net, 3 volumes,
# vsock) occupy slots 0x2-0x7. Their DSDT nodes are slot-populated markers only
# (device-type agnostic), so backing-free fillers reproduce them.
_EMULATED_SLOTS = range(0x2, 0x8)

# Launch-only QEMU flags the dumper drops: those taking a value (skipped whole)
# and bare toggles (skipped alone). -machine is rewritten; -serial is re-added
# as a metadata field.
_DROP_VALUE_FLAGS = frozenset({"-machine", "-name", "-drive", "-vga", "-serial", "-pidfile"})
_BARE_FLAGS = frozenset({"-nodefaults", "-nographic", "-daemonize"})


def _bars_arg(bars: list[PciBar]) -> str:
    """Format a BAR layout as the pci-bar-stub ``bars=`` value (``;``-separated)."""
    parts = []
    for b in bars:
        size = f"{b.size_mb // 1024}G" if b.size_mb % 1024 == 0 else f"{b.size_mb}M"
        parts.append(f"{b.index}:{size}:{b.kind}")
    return ";".join(parts)


def _rewrite_object(obj: str) -> str | None:
    """Rewrite a ``-object`` value for the dumper; None drops it."""
    if '"qom-type":"tdx-guest"' in obj or "qom-type=tdx-guest" in obj:
        return None  # no confidential-guest support in the dumper QEMU
    if obj.startswith("memory-backend-ram"):
        # Host NUMA binding is launch-only; reserve=off maps the (possibly multi-
        # TB) backend without allocating it. See the reserve=off finding.
        obj = re.sub(r",host-nodes=\d+", "", obj)
        obj = obj.replace(",policy=bind", "")
        if "reserve=" not in obj:
            obj += ",reserve=off"
    return obj


def _swap_endpoint(dev: str, profile: GpuProfile) -> str:
    """Swap a ``vfio-pci`` endpoint for a ``pci-bar-stub`` with the GPU's BARs."""
    bus = re.search(r"bus=([^,]+)", dev)
    if not bus:
        raise ValueError(f"vfio-pci device without a bus=: {dev!r}")
    rp = bus.group(1)
    if not re.fullmatch(r"rp\d+", rp):
        # NVSwitch (rp_nvsw*) / InfiniBand (rp_ib*) are passthrough devices too;
        # their BARs also shape the DSDT and need their own captured layout.
        raise NotImplementedError(
            f"endpoint on {rp!r} has no BAR layout yet — capture "
            f"lspci -vvvnn for that device type and extend the profile "
            f"(only GPU BARs are modeled today)"
        )
    if not profile.pci_bars:
        raise ValueError(
            f"profile {profile.name!r} has no pci_bars — run discover-profile.sh "
            f"on a host with this GPU and add the layout before generating"
        )
    device_id = int(profile.pci_device_ids[0], 16)
    return (
        f"pci-bar-stub,bus={rp},bars={_bars_arg(profile.pci_bars)},"
        f"vendor={_NVIDIA_VENDOR:#06x},device={device_id:#06x},class={_GPU_CLASS:#06x}"
    )


def _rewrite_devices(devices: list[str], profile: GpuProfile) -> list[str]:
    """Fillers for slots 0x2-0x7, then the passthrough topology with stubbed BARs."""
    fillers = [f"virtio-rng-pci,bus=pcie.0,addr={s:#x}" for s in _EMULATED_SLOTS]
    out: list[str] = list(fillers)
    for dev in devices:
        if dev.startswith("virtio-blk-pci,drive=virtio-disk0"):
            continue  # boot disk — replaced by the slot-fillers above
        if dev.startswith("vfio-pci"):
            out.append(_swap_endpoint(dev, profile))
        else:
            out.append(dev)  # pxb-pcie / pcie-root-port
    return out


def spec_to_metadata(
    spec: MachineSpec,
    profile: GpuProfile,
    *,
    acpi_tables: str,
    with_smbios: bool = True,
) -> dict:
    """Build the tdx-measure ``ImageConfig`` dict for ``spec`` (offline dump)."""
    args = build_qemu_command(spec)

    machine = _DUMP_MACHINE
    cpu = accel = memory = bios = smp = ""
    objects: list[str] = []
    numa: list[str] = []
    smbios: list[str] = []
    devices: list[str] = []
    fw_cfg: list[str] = []

    i, n = 0, len(args)
    while i < n:
        a = args[i]
        if a in _BARE_FLAGS or not a.startswith("-"):
            i += 1  # bare toggle, or the leading "qemu-system-x86_64"
            continue
        val = args[i + 1] if i + 1 < n else ""
        if a == "-accel":
            accel = val
        elif a == "-m":
            memory = val
        elif a == "-smp":
            smp = val
        elif a == "-cpu":
            cpu = val
        elif a == "-bios":
            bios = val
        elif a == "-object":
            obj = _rewrite_object(val)
            if obj is not None:
                objects.append(obj)
        elif a == "-numa":
            numa.append(val)
        elif a == "-smbios":
            smbios.append(val)
        elif a == "-device":
            devices.append(val)
        elif a == "-fw_cfg":
            fw_cfg.append(val)
        elif a not in _DROP_VALUE_FLAGS:
            raise ValueError(f"platform_tables: unhandled QEMU flag {a!r}")
        i += 2

    cpus = int(smp.split(",", 1)[0]) if smp else 0
    return {
        "boot_config": {
            "cpus": cpus,
            "memory": memory,
            "bios": bios,
            "acpi_tables": acpi_tables,
            "qemu": {
                "machine": machine,
                "cpu": cpu,
                "accel": accel,
                "smp": smp,
                "objects": objects,
                "numa": numa,
                "smbios": smbios if with_smbios else [],
                "serial": ["null"],  # adds COM1 to the DSDT
                "devices": _rewrite_devices(devices, profile),
                "fw_cfg": fw_cfg,
            },
        },
        "direct": {"kernel": "/dev/null", "initrd": "/dev/null", "cmdline": ""},
    }
