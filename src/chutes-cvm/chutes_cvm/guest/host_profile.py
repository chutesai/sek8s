"""The host profile: what ``discover-profile.sh`` observed, plus what follows from it.

The document is the single reader's output; this wraps it and derives everything the launch and
the measurement need. Two halves meet here:

  * the **observed** hardware -- ``GpuDevice`` / ``NvSwitchDevice`` / ``IbDevice`` lists, each
    device carrying its own identity, NUMA node and BAR layout;
  * the **authored** policy -- the ``GpuProfile`` that the GPUs' device id selects, holding what
    the host cannot report (VRAM and VBIOS are invisible once the GPUs are bound to vfio-pci) and
    what we decide (reserved CPUs, guest-RAM rule, which endpoints to attach).

Nothing is stored that can be computed: device counts, id sets and NUMA vectors are all
projections of the device lists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from chutes_cvm import proc
from chutes_cvm.guest.devices import GpuDevice, IbDevice, NvSwitchDevice, PciDevice
from chutes_cvm.guest.gpu.profiles import GPU_PROFILES, GpuProfile
from chutes_cvm.guest.qemu import (
    NumaPciTopologyState,
    PciTopologyState,
    QemuCommand,
    build_base_cmd,
    cpu_args_for_qemu_version,
)
from chutes_cvm.paths import SCRIPTS_DIR


def _node_sig(nodes: tuple[int, ...]) -> str:
    """Compact signature of a per-device NUMA-node vector: ``node{n}`` when every device sits on
    one node (``(0,0,0,0)`` -> ``node0``), else the raw vector (``(0,0,1,1)`` -> ``0011``).
    """
    if len(set(nodes)) == 1:
        return f"node{nodes[0]}"
    return "".join(str(n) for n in nodes)


@dataclass(frozen=True)
class HostCpu:
    """The host's CPU, as reported.

    Observed facts only -- ``vcpus`` and ``-smp`` are derived from these plus the GPU profile's
    reserve, and live on ``HostProfile``. (This is not the old ``CpuTopology``, which carried the
    derived vcpus and existed as a value object for a registry lookup the API now owns.)

    ``processor_id`` is CPUID leaf-1 as 8-byte hex; it becomes the SMBIOS Type-4 Processor ID and
    is None when unreadable, which offline generation refuses rather than measure its own host's
    CPU. ``vendor`` fixes the SRAT memory hole, which is AMD-guest-gated.
    """

    count: int
    sockets: int
    vendor: str
    processor_id: "str | None"

    @classmethod
    def from_dict(cls, d: dict) -> "HostCpu":
        """The wire calls it ``total``; a total of what is not obvious, so it lands as ``count``."""
        return cls(
            count=int(d.get("total") or 0),
            sockets=int(d.get("sockets") or 0),
            vendor=d.get("cpu_vendor") or "",
            processor_id=d.get("cpu_processor_id"),
        )


class HostProfile:
    """One host, as captured. Construct from the document ``discover-profile.sh`` emits."""

    def __init__(self, raw: dict):
        self.raw = raw

    @classmethod
    def from_host(cls) -> "HostProfile":
        """Read this host by running ``discover-profile.sh``.

        The script is the single reader of the hardware -- it stays standalone so an operator can
        run it without chutes-cvm installed, and so nothing else grows a second way to look at a
        host. Everything that reads a host goes through here: submit, preflight, and launch.

        It writes a JSON file and prints the path on its last stdout line; the file is transient,
        so it is read and removed.
        """
        script = SCRIPTS_DIR / "discover-profile.sh"
        if not script.exists():
            raise FileNotFoundError(f"discover-profile.sh not found at {script}")
        result = proc.run(
            ["bash", str(script), "--json-only"], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"discover-profile.sh failed: {result.stderr.strip() or 'no output'}"
            )
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        if not lines:
            raise RuntimeError("discover-profile.sh produced no JSON file path")
        path = Path(lines[-1].strip())
        try:
            raw = json.loads(path.read_text())
        finally:
            path.unlink(missing_ok=True)
        return cls(raw)

    # ── observed hardware ───────────────────────────────────────────────────
    @cached_property
    def gpus(self) -> tuple[GpuDevice, ...]:
        return GpuDevice.from_dicts(self.raw.get("gpus"))

    @cached_property
    def nvswitches(self) -> tuple[NvSwitchDevice, ...]:
        return NvSwitchDevice.from_dicts(self.raw.get("nvswitches"))

    @cached_property
    def ib_devices(self) -> tuple[IbDevice, ...]:
        return IbDevice.from_dicts(self.raw.get("ib_devices"))

    @property
    def gpu_count(self) -> int:
        return len(self.gpus)

    # ── host facts the devices do not carry ─────────────────────────────────
    @cached_property
    def cpu(self) -> HostCpu:
        return HostCpu.from_dict(self.raw.get("cpu") or {})

    @property
    def host_mem_gb(self) -> int:
        return int((self.raw.get("memory") or {}).get("total_gb") or 0)

    @property
    def numa_node_count(self) -> int:
        return int((self.raw.get("numa") or {}).get("node_count") or 0)

    @property
    def qemu_version(self) -> str:
        return (self.raw.get("qemu") or {}).get("qemu_version") or ""

    # ── the policy the hardware selects ─────────────────────────────────────
    @cached_property
    def gpu_profile(self) -> GpuProfile:
        """The ``GpuProfile`` this host's GPUs select.

        All GPUs must be one model: the profile carries a single device id, and one passthrough
        endpoint describes the whole platform. Checked against real per-device data rather than
        assumed from one representative.
        """
        ids = {g.device_id for g in self.gpus}
        if len(ids) != 1:
            raise ValueError(
                f"expected one GPU model, found {sorted(ids) or 'none'}; "
                "a host with mixed GPU device ids cannot be profiled"
            )
        device_id = ids.pop()
        for profile in GPU_PROFILES.values():
            if profile.matches_device_id(device_id):
                return profile
        raise ValueError(f"no GPU profile matches device id {device_id}")

    # ── what the launcher will attach ───────────────────────────────────────
    @property
    def uses_guest_numa(self) -> bool:
        """Whether the launcher builds the 2-node guest-NUMA topology rather than a flat one."""
        return self.gpu_profile.enable_numa_topology and self.numa_node_count == 2

    @cached_property
    def attached_nvswitches(self) -> tuple[NvSwitchDevice, ...]:
        """NVSwitches the launcher passes through -- all of them, or none.

        A profile that requires them on a host reporting none is refused rather than launched
        without them: the guest would come up with a different PCI topology than the one its
        class was measured for, so it could not attest.
        """
        if not self.gpu_profile.should_passthrough_nvswitches(self.gpu_count):
            return ()
        if not self.nvswitches:
            raise ValueError(
                f"profile {self.gpu_profile.name!r} requires NVSwitches for "
                f"{self.gpu_count} GPU(s) but this host reports none. Verify with: "
                f"lspci -Dnn | grep '\\[10de:22a3\\]'"
            )
        return self.nvswitches

    @cached_property
    def attached_ib(self) -> tuple[IbDevice, ...]:
        """IB PFs the launcher passes through.

        Bridge PFs are excluded: on B200/B300 HGX they carry the NVSwitch fabric management
        (VPD ``SMDL=SW_MNG``) and must stay on the host for Fabric Manager. VFs are excluded
        because a VF is not a PF.
        """
        if not self.gpu_profile.should_passthrough_infiniband:
            return ()
        return tuple(d for d in self.ib_devices if not d.is_bridge_pf and not d.is_vf)

    # ── the guest that produces ─────────────────────────────────────────────
    @property
    def vcpus(self) -> int:
        """Guest vCPUs: the host's CPUs less the profile's reserve for the host OS."""
        return self.cpu.count - self.gpu_profile.host_reserved_cpus

    @property
    def guest_mem_gb(self) -> int:
        """Guest RAM, per the profile's sizing rule -- NOT a function of host RAM alone."""
        return self.gpu_profile.guest_mem_gb(self.host_mem_gb, self.gpu_count)

    @property
    def smp_topology(self) -> str:
        """QEMU ``-smp``. threads=1 disables guest SMT (each vCPU a core)."""
        return (
            f"{self.vcpus},sockets={self.cpu.sockets},"
            f"cores={self.vcpus // self.cpu.sockets},threads=1"
        )

    @property
    def mem(self) -> str:
        """QEMU ``-m``."""
        return f"{self.guest_mem_gb}G"

    @staticmethod
    def _nodes(devices: "tuple[PciDevice, ...]") -> tuple[int, ...]:
        """Per-device NUMA node. The lists are already in BDF order, which is significant."""
        return tuple(d.numa_node for d in devices)

    @property
    def gpu_numa_nodes(self) -> tuple[int, ...]:
        return self._nodes(self.gpus)

    @property
    def nvswitch_numa_nodes(self) -> tuple[int, ...]:
        return self._nodes(self.attached_nvswitches)

    @property
    def ib_numa_nodes(self) -> tuple[int, ...]:
        return self._nodes(self.attached_ib)

    @property
    def variant_label(self) -> str:
        """Deterministic variant id: ``<path>-<vcpus>c-<mem>g[-devices]``, e.g.
        ``numa-176c-1944g`` or ``numa-124c-1128g-nvsw-node0``.

        On the NUMA path the extra parts carry each device class's node signature; on the flat
        path only counts matter, because there is no PXB grouping to differ.
        """
        path = "numa" if self.uses_guest_numa else "flat"
        parts = [path, f"{self.vcpus}c-{self.guest_mem_gb}g"]
        if self.uses_guest_numa:
            if self.nvswitch_numa_nodes:
                parts.append("nvsw-" + _node_sig(self.nvswitch_numa_nodes))
            if self.ib_numa_nodes:
                parts.append("ib-" + _node_sig(self.ib_numa_nodes))
        else:
            if self.attached_nvswitches:
                parts.append(f"nvsw{len(self.attached_nvswitches)}")
            if self.attached_ib:
                parts.append(f"ib{len(self.attached_ib)}")
        return "-".join(parts)

    # ── the command it launches with ────────────────────────────────────────
    @property
    def passthrough_devices(self) -> "tuple[PciDevice, ...]":
        """Every endpoint the launcher attaches, in root-port order: GPUs, NVSwitches, IB."""
        return self.gpus + self.attached_nvswitches + self.attached_ib

    @property
    def cpu_args(self) -> str:
        """The ``-cpu`` string this host launches with, from the QEMU version it reports."""
        return cpu_args_for_qemu_version(self.qemu_version)

    def qemu_command(
        self,
        *,
        firmware: str,
        cpu_args: "str | None" = None,
        img_path: str = "root.qcow2",
        process_name: str = "chutes-td",
        foreground: bool = False,
        pidfile: str = "/dev/null",
        logfile: str = "/dev/null",
        kernel_path: str = "/dev/null",
        initrd_path: str = "/dev/null",
        cmdline: str = "",
    ) -> QemuCommand:
        """The QEMU command this host launches with.

        Native throughout: the endpoints carry the devices' real BDFs and ``-cpu`` is the launch
        form, because this is the command a launch would run. The measurement adapter makes its
        own substitutions afterwards -- swapping each ``vfio-pci`` endpoint for a
        ``pci-bar-stub`` and pinning the CPU identity -- so nothing offline leaks in here.

        Root ports are numbered per kind in device order -- ``rp1..rpN`` for GPUs, then
        ``rp_nvsw*``, then ``rp_ib*`` -- and chassis numbers run across all of them, which is the
        ordering the guest PXB grouping and therefore RTMR0 depend on.
        """
        numa = self.uses_guest_numa
        cmd = build_base_cmd(
            mem=self.mem,
            smp_topology=self.smp_topology,
            process_name=process_name,
            cpu_args=cpu_args if cpu_args is not None else self.cpu_args,
            firmware=firmware,
            img_path=img_path,
            foreground=foreground,
            pidfile=pidfile,
            logfile=logfile,
            host_nodes=[0, 1] if numa else [],
            kernel_path=kernel_path,
            initrd_path=initrd_path,
            cmdline=cmdline,
        )
        topology = NumaPciTopologyState() if numa else PciTopologyState()
        chassis = 0
        for prefix, devices in (
            ("rp", self.gpus),
            ("rp_nvsw", self.attached_nvswitches),
            ("rp_ib", self.attached_ib),
        ):
            for ordinal, device in enumerate(devices, start=1):
                chassis += 1
                kwargs: dict = {"rp_id": f"{prefix}{ordinal}", "chassis": chassis}
                if numa:
                    kwargs["numa_node"] = device.numa_node
                topology.add_device(cmd, host_bdf=device.bdf, **kwargs)
        return cmd

    # ── serialisation ───────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """The document as submitted, with the device lists normalised."""
        return self.raw | {
            "gpus": [d.to_dict() for d in self.gpus],
            "nvswitches": [d.to_dict() for d in self.nvswitches],
            "ib_devices": [d.to_dict() for d in self.ib_devices],
        }

    def to_json(self) -> str:
        """Compact separators keep the signed body small; key order is irrelevant to the API."""
        return json.dumps(self.to_dict(), separators=(",", ":"))
