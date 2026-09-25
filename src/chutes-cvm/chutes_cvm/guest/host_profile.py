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

import copy
import itertools
import json
from dataclasses import dataclass, fields
from functools import cached_property
from pathlib import Path

from chutes_cvm import proc
from chutes_cvm.guest.detection import GUEST_CPU_ARGS
from chutes_cvm.guest.devices import GpuDevice, IbDevice, NvSwitchDevice, PciDevice
from chutes_cvm.guest.gpu.profiles import GPU_PROFILES, HOST_RESERVED_CPUS, GpuProfile
from chutes_cvm.guest.tee import TeeProvider, provider_for_cpu_vendor
from chutes_cvm.paths import SCRIPTS_DIR


def _node_sig(nodes: tuple[int, ...]) -> str:
    """Compact signature of a per-device NUMA-node vector: ``node{n}`` when every device sits on
    one node (``(0,0,0,0)`` -> ``node0``), else the raw vector (``(0,0,1,1)`` -> ``0011``).
    """
    if len(set(nodes)) == 1:
        return f"node{nodes[0]}"
    return "".join(str(n) for n in nodes)


#: Host RAM the guest never takes. TDX guest memory is pinned and unreclaimable, so the guest
#: must leave the host enough for the host OS, the TDX PAMT, page tables and VFIO DMA pinning --
#: otherwise the kernel OOM-kills QEMU (the whole VM) as the guest faults in pages.
#:
#: Flat, deliberately not a percentage. A percentage breaks down at scale: 12% over-reserved
#: ~360 GB on a 3 TB host and wrongly rejected valid launches, and any fraction re-introduces the
#: same mismatch once a host exceeds (reserve / fraction). The only overhead that scales with
#: guest size is the TDX PAMT (~0.4%), and 64 GB covers PAMT for guests up to ~16 TB.
VM_MEM_RESERVE_GB = 64

#: The least of its GPUs' aggregate VRAM a guest may be given. Guest RAM is sized to VRAM because
#: that is roughly what a workload needs to stage and feed the GPUs; a guest far below it thrashes
#: rather than fails, so it has to be refused rather than launched slowly. Set with headroom over
#: the tightest real host -- a B300 2 TB sled reaches 84% -- so a supported machine is never
#: rejected, while a host that cannot come close is.
MIN_VRAM_FRACTION = 0.7


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
    def keys(cls) -> tuple[str, ...]:
        """The document keys a cpu block must carry: this class's fields.

        One spelling here and on the wire -- the capture used to say
        ``total``/``cpu_vendor``/``cpu_processor_id`` while this said the other thing, and a
        round-tripped profile came back ``count=0, processor_id=None``: a guest with no CPUs.
        """
        return tuple(f.name for f in fields(cls))

    @classmethod
    def from_dict(cls, d: dict) -> "HostCpu":
        """The cpu block, or raise naming what is absent.

        No defaults: a host has no 0 CPUs and no 0 sockets, so defaulting one turns a broken
        capture into a profile that measures a machine nobody has. ``processor_id`` may be null --
        it is unreadable on some hosts -- but the key must be there, and offline generation
        refuses a null rather than measuring its own host's CPU.
        """
        missing = [k for k in cls.keys() if k not in d]
        if missing:
            raise ValueError(f"cpu block missing {', '.join(missing)}: {d!r}")
        return cls(
            count=int(d["count"]),
            sockets=int(d["sockets"]),
            vendor=str(d["vendor"]),
            processor_id=d["processor_id"],
        )


class HostProfile:
    """One host, as captured. Construct from the document ``discover-profile.sh`` emits."""

    #: The guest ``-cpu`` per host QEMU version, in the LAUNCH form -- offline generation adds an
    #: explicit CPU identity on top (see image_config). One entry today: 10.2.1 ships with 26.04,
    #: the only supported host OS. Any other version takes the same form.
    CPU_ARGS_BY_QEMU = {"10.2.1": GUEST_CPU_ARGS}

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
        profile = cls(raw)
        # The one place guest RAM is derived; everything downstream carries it. The script
        # cannot do it: VRAM is unreadable once the GPUs are bound to vfio-pci, so it comes
        # from the profile.
        raw["memory"]["guest_gb"] = profile._derived_guest_mem_gb
        return profile

    @property
    def _derived_guest_mem_gb(self) -> int:
        """Guest RAM: as close to aggregate VRAM as the host can back, keeping its own reserve.

        One rule for every profile. Guest RAM should approximate the VRAM it serves, so the VRAM
        total is the target and host RAM is only ever a ceiling -- never a target in its own
        right. Treating it as one is what gave two B200 hosts differing only in RAM (2013 and
        3023 GB) two guests, two classes and two measurements for one piece of hardware.

        The reserve binds only where VRAM exceeds host RAM -- a B300 2 TB sled against 8x288 GB.
        A profile in that state tracks host RAM again, so two such sleds can still measure apart.

        ``gpu_count`` is every GPU the host reports, which is also every GPU the launcher binds:
        passthrough is all-or-nothing today. If that ever changes, guest RAM follows the attached
        set, not the detected one, and this is one of the places that has to move.

        Called once, by ``from_host``. Everything downstream reads the stored answer.
        """
        if not self.gpus:
            # No VRAM to approximate, so host RAM is the only input -- all of it bar the reserve.
            # Nothing publishes a measurement for a GPU-less class, so this guest is a debug one;
            # sizing it from the host beats a fixed number that fits no particular machine.
            return self.host_mem_gb - VM_MEM_RESERVE_GB

        gpus = self.gpu_count
        vram_gb = self.gpu_profile.vram_gb
        total_vram_gb = vram_gb * gpus
        # Per GPU, so the total is divisible by the GPU count and vcpu/mem stay
        # socket-divisible. The host's share floors; VRAM is already a whole number per GPU.
        per_gpu_gb = min(vram_gb, (self.host_mem_gb - VM_MEM_RESERVE_GB) // gpus)
        guest_gb = per_gpu_gb * gpus
        floor_gb = int(total_vram_gb * MIN_VRAM_FRACTION)
        if guest_gb < floor_gb:
            raise ValueError(
                f"host has {self.host_mem_gb}G RAM, which backs a {guest_gb}G guest for "
                f"{gpus}x {self.gpu_profile.name} ({total_vram_gb}G VRAM) -- under the "
                f"{MIN_VRAM_FRACTION:.0%} floor of {floor_gb}G. This host is too small for these "
                f"GPUs."
            )
        return guest_gb

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
        return HostCpu.from_dict(self.raw["cpu"])

    @property
    def host_mem_gb(self) -> int:
        return int(self.raw["memory"]["total_gb"])

    @property
    def numa_node_count(self) -> int:
        return int(self.raw["numa"]["node_count"])

    @property
    def qemu_version(self) -> str:
        return str(self.raw["qemu"]["qemu_version"])

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
        """Whether the launcher builds the 2-node guest-NUMA topology rather than a flat one.

        Hardware only, and only the CPU's: guest NUMA is vCPUs grouped into nodes, per-node
        memory bound to the matching host node, and a distance matrix. That pays off whenever
        the host has nodes to bind to, whatever is plugged into it. Two is the cap because two
        is all the builder can express -- a 4-node guest would need 4 sockets and an NxN SLIT.

        PXB-PCIe grouping rides on the same decision, putting each passthrough device on the
        guest node its host node maps to. That is a bonus, not the reason: a host whose GPUs all
        sit on one node still wants node-local memory for its vCPUs.

        The GPU model has no say. ``GpuProfile.enable_numa_topology`` used to gate this, but it
        recorded a host fact ("2 nodes, GPUs split 4+4, confirmed on <hostname>") on a GPU class,
        left over from when GpuProfile was the host profile.

        The platform does: the host offering two nodes is necessary, not sufficient. SEV-SNP
        guests cannot boot on per-node backends yet (see ``SnpTeeProvider.supports_guest_numa``),
        so an AMD class of the same shape takes the flat path.
        """
        return self.numa_node_count == 2 and self.tee_provider.supports_guest_numa

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
        """Guest vCPUs: the host's CPUs less the reserve for the host OS.

        The reserve is the GPU profile's where there is one (a heavier fixed host workload, e.g.
        FabricManager, keeps more); a host with no GPUs runs none of that and takes the base.
        """
        reserved = (
            self.gpu_profile.host_reserved_cpus if self.gpus else HOST_RESERVED_CPUS
        )
        return self.cpu.count - reserved

    @property
    def guest_mem_gb(self) -> int:
        """Guest RAM: read, never derived.

        Resolved once, by ``from_host``, at the moment the host is read -- the sizing rules are
        per-GpuProfile, so a later release deriving a different answer would measure one guest and
        file it under a key computed from another. Every document downstream of a capture carries
        the answer.
        """
        return int(self.raw["memory"]["guest_gb"])

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
        return self.CPU_ARGS_BY_QEMU.get(self.qemu_version, GUEST_CPU_ARGS)

    def verify_environment(self) -> None:
        """Raise unless this host's environment can run the guest this profile describes.

        Host readiness asked OF the profile rather than re-derived beside it. The platform
        follows from the captured CPU vendor, and the provider knows both which kvm parameter
        reports it enabled and what an operator should change when it is not -- so nothing
        else grows a second way to look at a host.

        Named for the environment rather than the launch on purpose: a profile describes a
        machine and knows nothing about booting a guest. It delegates to the provider method
        of the same name, which is the whole of the check today.
        """
        self.tee_provider.verify_environment()

    @property
    def tee_provider(self) -> TeeProvider:
        """The confidential-computing platform this host class runs.

        Derived from the CPU vendor, not detected: a class is Intel or AMD silicon, so the
        profile already determines the TEE, the firmware it boots and the guest object it
        launches with. Nothing needs to be passed in or re-detected alongside the profile.

        This is the class's identity, NOT whether the platform is enabled on the machine in
        front of you -- SEV-SNP can be off in BIOS on AMD silicon. The provider answers that
        separately (``TeeProvider.verify_environment``) and reports it as a host problem.
        """
        return provider_for_cpu_vendor(self.cpu.vendor)

    # ── serialisation ───────────────────────────────────────────────────────
    @classmethod
    def from_api_profile(cls, doc: dict) -> "HostProfile":
        """A stored profile, read back to generate its measurement.

        The API keeps only what reaches RTMR0, so the devices arrive without host addresses. A
        device has no identity without one, so positional stand-ins are filled in here -- at the
        boundary, where they are known to be meaningless -- rather than by relaxing the capture's
        invariant. Generation swaps every endpoint for a ``pci-bar-stub`` keyed on its root port,
        so nothing downstream reads them.

        What comes back describes a guest to measure, not a host to launch. Launching needs real
        addresses and must start from ``from_host``.
        """
        doc = copy.deepcopy(doc)
        slots = itertools.count()
        for key in ("gpus", "nvswitches", "ib_devices"):
            for device in doc.get(key) or ():
                device["bdf"] = f"{next(slots):04x}:00:00.0"
        return cls(doc)

    def to_api_profile(self) -> dict:
        """This host as the API stores it: exactly the RTMR0 determinants.

        Stored == hashed == required, one set. An unhashed field stored beside a hashed one
        re-splits the class at the byte level -- two hosts that measure identically would differ
        in the stored row, so the API's first-write-wins would silently drop one of them. That is
        why host RAM, BDFs, DMI and the lspci tree are absent: none reach RTMR0, so none may be
        stored. The capture keeps all of it; this is only what crosses the wire.

        NVSwitch and IB are the ATTACHED sets, not the inventory -- what the launcher passes
        through is what gets measured, so the gating happens once, here.
        """
        return {
            "gpus": [d.to_api_dict() for d in self.gpus],
            "nvswitches": [d.to_api_dict() for d in self.attached_nvswitches],
            "ib_devices": [d.to_api_dict() for d in self.attached_ib],
            "cpu": {
                "count": self.cpu.count,
                "sockets": self.cpu.sockets,
                "vendor": self.cpu.vendor,
                "processor_id": self.cpu.processor_id,
            },
            "memory": {"guest_gb": self.guest_mem_gb},
            "numa": {"node_count": self.numa_node_count},
            "qemu": {"qemu_version": self.qemu_version},
        }

    def to_api_json(self) -> str:
        """``to_api_profile`` as the signed request body."""
        return json.dumps(self.to_api_profile(), separators=(",", ":"), sort_keys=True)

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
