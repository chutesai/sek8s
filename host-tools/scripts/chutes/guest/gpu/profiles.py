"""GPU profile registry: per-GPU-type passthrough behavior.

Each supported GPU model is a GpuProfile subclass that encodes BAR sizes,
CC/PPCIe mode configuration, NVSwitch policy, and InfiniBand policy.
Adding a new GPU type requires one subclass and one GPU_PROFILES entry.

## Adding a new GPU profile

A GpuProfile holds only GPU-MODEL policy that is identical on every host the GPU
ships on. The host-instance facts that feed RTMR0 — the guest -smp (vcpus +
sockets), guest RAM, and CPU identity (vendor + SMBIOS Type-4 Processor ID) — are
NOT profile constants: they live on the topology fingerprint (gpu/topology.py),
detected from the live host and declared in ``baselined_measurements``. So "same
GPU, different CPU/RAM host" is two fingerprints of one profile, not two profiles
(e.g. B200 on a 192-CPU Xeon vs a 288-CPU Xeon 6).

To add a profile:
  1. Encode GPU-model policy on the subclass: pci_device_ids, BAR/VRAM, CC/PPCIe
     mode, NVSwitch/IB policy, firmware. Override ``host_reserved_cpus`` if the
     host runs a heavy fixed workload (B200 = 16 for FabricManager; default 4);
     override ``guest_mem_gb`` if guest RAM is derived from host RAM rather than
     pinned to aggregate VRAM (B200 does, most don't). Keep host_reserved_cpus EVEN
     so vcpus divides across sockets.
  2. Run ``discover-profile.sh`` on each host CLASS the GPU ships on to capture its
     shape (cpu_vendor, cpu_processor_id, plus the CPU/RAM the fingerprint's
     vcpus/mem derive from), and declare one fingerprint per class in
     ``baselined_measurements`` (see H200Profile). A fingerprint with
     cpu_processor_id=None is launch-gated but not yet generatable — fill it in from
     discover-profile before generating that class's measurement.

Changing host_reserved_cpus / guest_mem_gb moves the fingerprint's vcpus/mem →
RTMR0, so it requires re-baselining that profile's attestation policy.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from chutes.guest.gpu import known_topologies as known
from chutes.guest.gpu.topology import TopologyFingerprint

HOST_RESERVED_CPUS = 4


@dataclass(frozen=True)
class PciBar:
    """One PCI Base Address Register: index, size, and type.

    Read from ``lspci -vvvnn`` (the ``Region N:`` lines, plus the Physical
    Resizable BAR block for the current VRAM size). ``kind`` is
    ``m32``/``m64``/``p32``/``p64`` — (m)em non-prefetchable / (p)refetchable,
    32- or 64-bit addressing. A 64-bit BAR consumes two BAR slots, so a card
    with three 64-bit BARs reports them at indices 0/2/4.

    Offline measurement generation reproduces these BARs with a ``pci-bar-stub``
    device so the guest DSDT's MMIO windows match a real passthrough launch
    without the hardware present.
    """

    index: int
    size_mb: int
    kind: str


@dataclass
class PassthroughDevice:
    """A passthrough endpoint reproduced offline as a ``pci-bar-stub``: its PCI vendor,
    device id, class, and BAR layout, all from ``lspci -vvvnn``. Keyed by endpoint kind
    ("gpu"/"nvswitch"/"ib") in ``GpuProfile.passthrough``.
    """

    vendor: int  # e.g. 0x10DE (NVIDIA), 0x15B3 (Mellanox / IB)
    device_id: str  # hex, e.g. "22a3"
    pci_class: int  # e.g. 0x0680
    bars: list[PciBar]


class GpuProfile(ABC):
    """Base class for GPU-type-specific passthrough behavior."""

    # PCI device IDs that identify this GPU (e.g. [10de:2901] -> 2901). Override in subclass.
    # Drives profile DETECTION at launch (matches_device_id); the offline stub id for the
    # GPU lives in passthrough["gpu"].
    pci_device_ids: list[str] = []

    # Human-facing hardware identity for the generated teeMeasurements entry
    # (e.g. "8xh200"), combined with the QEMU version + the topology's computed
    # variant_label to form each hardware `name`. expected_gpus is the GPU-type
    # id(s) surfaced in that entry. Override both per subclass.
    display_name: str = ""
    expected_gpus: list[str] = []

    # Every passthrough endpoint reproduced offline as a pci-bar-stub, keyed by the bus kind
    # _swap_endpoint matches: "gpu" (rp\d+), "nvswitch" (rp_nvsw*), "ib" (rp_ib*). Captured
    # from `lspci -vvvnn` (discover-profile.sh). No "gpu" entry = not yet modeled for this
    # model, so offline measurement generation is unavailable (not broken) until it is added.
    passthrough: dict[str, PassthroughDevice] = {}

    def matches_device_id(self, device_id: str) -> bool:
        """Return True if device_id matches this profile's pci_device_ids."""
        device_id = device_id.lower()
        return any(device_id == pid.lower() for pid in self.pci_device_ids)

    @property
    @abstractmethod
    def name(self) -> str:
        """Short model identifier (e.g. 'B200', 'H200')."""
        ...

    @property
    @abstractmethod
    def bar_size_mb(self) -> int:
        """MMIO BAR size in MB for QEMU fw_cfg hint (when use_ovmf_mmio_fw_cfg is True)."""
        ...

    @property
    def use_ovmf_mmio_fw_cfg(self) -> bool:
        """Whether to pass opt/ovmf/X-PciMmio64Mb* fw_cfg hints per GPU to QEMU.

        B300 disables this: 8×512 GiB BARs need a multi-TB aggregate MMIO window that
        OVMF auto-sizes; per-GPU fw_cfg hints can prevent correct BAR assignment.
        """
        return True

    @property
    @abstractmethod
    def vram_gb(self) -> int:
        """VRAM per GPU in GB. Used to size VM RAM as gpu_count * vram_gb."""
        ...

    @property
    def host_reserved_cpus(self) -> int:
        """Logical CPUs kept for the host OS (not handed to the guest).

        Defaults to HOST_RESERVED_CPUS. Override per profile when the host
        carries a heavier fixed workload (e.g. FabricManager on NVSwitch HGX
        systems). Must be even so vcpus divides across sockets. Detection uses it
        to derive the guest vcpus (host_cpus − this) for the fingerprint; changing
        it changes vcpus → the fingerprint → RTMR0, so re-baseline attestation.
        """
        return HOST_RESERVED_CPUS

    # The host-instance facts that feed RTMR0 — the guest -smp (vcpus + sockets),
    # guest RAM, and CPU identity (vendor + SMBIOS Type-4 Processor ID) — are NOT
    # profile constants: they vary host to host and live on the topology fingerprint
    # (gpu/topology.py). Detection derives them from the LIVE host (vcpus =
    # host_cpus − host_reserved_cpus; sockets; mem via guest_mem_gb; CPU via
    # /proc/cpuinfo) and matches the result against baselined_measurements. The
    # profile supplies only host_reserved_cpus (workload policy) and guest_mem_gb.

    @abstractmethod
    def get_cc_mode_args(self, total_gpus: int) -> list[list[str]]:
        """Return nvidia-gpu-tools argument lists for CC/PPCIe mode configuration.

        Each inner list is one nvidia-gpu-tools invocation's arguments.
        """
        ...

    def get_sbr_reset_args(self) -> list[str]:
        """Return nvidia-gpu-tools args for a Secondary Bus Reset recovery.

        CC-mode GPUs (B200, B300, RTX) use --reset-after-cc-mode-switch.
        H200 8-GPU PPCIe configs use --reset-after-ppcie-mode-switch.
        """
        return ["--reset-with-sbr", "--reset-after-cc-mode-switch"]

    @abstractmethod
    def should_passthrough_nvswitches(self, total_gpus: int) -> bool:
        """Whether NVSwitch devices should be detected and passed through."""
        ...

    @property
    def should_passthrough_infiniband(self) -> bool:
        """Whether InfiniBand devices should be detected and passed through."""
        return False

    def guest_mem_gb(self, host_gb: int, gpu_count: int) -> int:
        """Total guest RAM in GB for ``gpu_count`` GPUs on a host with ``host_gb`` RAM.

        The default pins guest RAM to aggregate VRAM (host RAM irrelevant). Override
        when a GPU type is deployed on hosts with more RAM than VRAM and should use it
        (e.g. B200). Detection bakes the result into the fingerprint's ``mem_gb``, so
        it feeds RTMR0 — changing the rule re-baselines attestation.
        """
        return self.vram_gb * gpu_count

    @property
    def enable_numa_topology(self) -> bool:
        """Use guest NUMA nodes, per-node memory bind, and PXB-PCIe grouping."""
        return False

    @property
    def baselined_measurements(self) -> dict[str, set[TopologyFingerprint]]:
        """QEMU version -> known topology fingerprints (RTMR0 = f(topology, QEMU)).

        Fingerprints are NumaTopology / FlatTopology value types (see
        gpu/topology.py). verify-host uses the per-QEMU keys to flag a topology
        with no measurement at a given QEMU. Empty dict = profile not
        characterized yet.
        """
        return {}

    @property
    def baselined_topologies(self) -> set[TopologyFingerprint]:
        """Union of known fingerprints across QEMU versions, for the launch-time
        hard-match (QEMU-agnostic). Empty union skips the check."""
        out: set[TopologyFingerprint] = set()
        for topos in self.baselined_measurements.values():
            out |= topos
        return out

    @property
    def enable_post_launch_tuning(self) -> bool:
        """Tune host CPU power and pin QEMU vCPU threads after launch."""
        return False

    @property
    def requires_fabric_manager(self) -> bool:
        """Whether the host Fabric Manager must be running before launch.

        True for NVSwitch-based HGX systems (B200, B300) where FM manages the
        NVSwitch fabric. FM must be active before CC mode SBR to ensure GPUs
        properly re-initialize NVLink connections after each reset.
        """
        return False

    @property
    def firmware_filename(self) -> str:
        """TDVF firmware filename in the repo firmware/ directory.

        Changing the firmware changes MRTD — attestation policy must be
        re-baselined for any profile using a different image.
        """
        # Built from edk2 Config-B (IntelTdxX64.dsc), no Secure Boot.
        # Run firmware/build-firmware.sh to rebuild from source.
        return "OVMF.inteltdx.fd"

    def describe_mode(self, total_gpus: int) -> str:
        """Human-readable description of the mode for logging."""
        return f"{self.name} passthrough"


class B200Profile(GpuProfile):
    """B200 (GPU-model policy). Covers both Intel host classes it ships on — a
    192-CPU/~2 TB Xeon and a 288-CPU/~3 TB Xeon 6 — as two fingerprints of this one
    profile (see baselined_measurements), not two classes. 2 NUMA nodes with GPUs
    split 4+4 across sockets. Confirmed from discover-profile.sh on am-b200-57
    (Xeon) and chutes-miner-gpu-0 (Xeon 6).
    """

    pci_device_ids = ["2901"]
    display_name = "8xb200"
    expected_gpus = ["b200"]

    @property
    def name(self) -> str:
        return "B200"

    @property
    def bar_size_mb(self) -> int:
        # 256 GiB: confirmed from lspci Region 2 on am-b200-34 reference host.
        return 262144

    @property
    def vram_gb(self) -> int:
        return 192  # B200 HBM3e

    def guest_mem_gb(self, host_gb: int, gpu_count: int) -> int:
        # B200 hosts carry far more RAM than VRAM, so guest RAM is DERIVED from the
        # host: leave ~64 GB for the host OS, floor-divide the rest per GPU (the floor
        # absorbs few-GB same-tier variance), re-multiply. ~2 TB host → 243/GPU → 1944
        # total; ~3 TB Xeon 6 host → 369/GPU → 2952 total. This derivation is what makes
        # "B200 vs Xeon 6" two fingerprints of one profile rather than two classes.
        return ((host_gb - 64) // gpu_count) * gpu_count

    @property
    def host_reserved_cpus(self) -> int:
        # 16 logical (8 physical cores, 4/socket). The host runs FabricManager
        # alongside QEMU's iothreads/vhost workers; the default reserve of 4 starves
        # them under heavy NVLink/NCCL I/O, surfacing as cudaErrorNvlinkUncorrectable
        # in the guest. The reserved cores also widen the gap the iothreads pin into
        # (see post_launch.py). Even, so vcpus stays socket-divisible. Applies to both
        # the 192-CPU Xeon and 288-CPU Xeon 6 host classes.
        return 16

    def get_cc_mode_args(self, total_gpus: int) -> list[list[str]]:
        return [["--set-cc-mode=on", "--reset-after-cc-mode-switch"]]

    def should_passthrough_nvswitches(self, total_gpus: int) -> bool:
        return False

    @property
    def should_passthrough_infiniband(self) -> bool:
        # Off (like H200/B300): guest networking is virtio-net, NVLink fabric is
        # host-side FM. Passing IB only made RTMR0 vary by NIC loadout.
        return False

    @property
    def enable_numa_topology(self) -> bool:
        # Host has 2 NUMA nodes with GPUs split 4+4 across sockets. (A Xeon 6 SNC3
        # host exposes 6 nodes, so use_numa_topology falls back to flat there; the
        # flag stays True so it activates when SNC is off / 2-node.)
        return True

    @property
    def enable_post_launch_tuning(self) -> bool:
        return True

    @property
    def requires_fabric_manager(self) -> bool:
        return True

    @property
    def baselined_measurements(self) -> dict[str, set[TopologyFingerprint]]:
        # ONE profile, two host classes — same 4+4 GPU layout, different CPU/RAM (the
        # B200-vs-Xeon6 collapse: two fingerprints, not two classes). Both Intel; each
        # cpu_processor_id PENDING a discover-profile.sh capture. QEMU 10.2.1 (26.04).
        return {"10.2.1": {known.B200_XEON_FP, known.B200_XEON6_FP}}

    def describe_mode(self, total_gpus: int) -> str:
        return "CC mode (B200)"


class B300Profile(GpuProfile):
    pci_device_ids = ["3182"]  # GB110 [B300 SXM6 AC]
    display_name = "8xb300"
    expected_gpus = ["b300"]

    @property
    def name(self) -> str:
        return "B300"

    @property
    def bar_size_mb(self) -> int:
        # 512 GiB: confirmed from lspci Region 2 on am-b300-61.
        return 524288

    @property
    def vram_gb(self) -> int:
        return 288  # B300 HBM3e (SXM6 AC)

    # Host: 2 sockets x 48 cores x 2 threads = 192 (Intel, from lscpu on am-b300-61)
    # → 188 vcpus. No baselined_measurements yet (uncharacterized): run
    # discover-profile.sh on a B300 host and declare its fingerprint (see H200Profile).

    def get_cc_mode_args(self, total_gpus: int) -> list[list[str]]:
        return [["--set-cc-mode=on", "--reset-after-cc-mode-switch"]]

    def should_passthrough_nvswitches(self, total_gpus: int) -> bool:
        return False

    @property
    def should_passthrough_infiniband(self) -> bool:
        # B300 HGX: every ConnectX-7 IB-class PF (15b3:1021, PCI class 0207) is an
        # NVSwitch bridge (SMDL=SW_MNG) and must stay on the host for Fabric Manager.
        # Remaining CX7 data NICs are Ethernet-class (0200), not IB passthrough targets.
        # Guest networking uses virtio-net; GPU fabric is NVLink via host-side FM.
        return False

    @property
    def use_ovmf_mmio_fw_cfg(self) -> bool:
        return False

    def describe_mode(self, total_gpus: int) -> str:
        return "CC mode (B300)"

    @property
    def requires_fabric_manager(self) -> bool:
        return True


class H200Profile(GpuProfile):
    pci_device_ids = ["2335"]  # H200 SXM (GH100)
    display_name = "8xh200"
    expected_gpus = ["h200"]
    # lspci -vvvnn on dev-h200-tee: GPU 10de:2335 (BAR2 resizable, 256G) + NVSwitch
    # 10de:22a3 class 0680 (single 32M BAR).
    passthrough = {
        "gpu": PassthroughDevice(
            0x10DE,
            "2335",
            0x0302,
            [PciBar(0, 16, "p64"), PciBar(2, 262144, "p64"), PciBar(4, 32, "p64")],
        ),
        "nvswitch": PassthroughDevice(0x10DE, "22a3", 0x0680, [PciBar(0, 32, "m64")]),
    }

    @property
    def name(self) -> str:
        return "H200"

    @property
    def bar_size_mb(self) -> int:
        return 262144  # 256GB

    @property
    def vram_gb(self) -> int:
        return 141  # H200 HBM3e

    @property
    def enable_numa_topology(self) -> bool:
        # Host has 2 NUMA nodes with GPUs split 4+4 across sockets.
        # Confirmed from discover-profile.sh on dev-h200-tee.
        return True

    @property
    def enable_post_launch_tuning(self) -> bool:
        return True

    def get_cc_mode_args(self, total_gpus: int) -> list[list[str]]:
        if total_gpus == 8:
            return [
                ["--set-cc-mode=off", "--reset-after-cc-mode-switch"],
                ["--set-ppcie-mode=on", "--reset-after-ppcie-mode-switch"],
            ]
        return [
            ["--set-ppcie-mode=off", "--reset-after-ppcie-mode-switch"],
            ["--set-cc-mode=on", "--reset-after-cc-mode-switch"],
        ]

    def get_sbr_reset_args(self) -> list[str]:
        return ["--reset-with-sbr", "--reset-after-ppcie-mode-switch"]

    def should_passthrough_nvswitches(self, total_gpus: int) -> bool:
        # HGX H200 SXM5: NVSwitches present and passed through for 8-GPU configs.
        # NOTE: discover-profile.sh on dev-h200-tee detected 0 NVSwitches — the
        # detection regex may miss HGX NVSwitch device IDs, or this host is a PCIe
        # H200 variant. Verify with `lspci | grep -i switch` on a confirmed HGX host
        # before changing this value.
        return total_gpus == 8

    @property
    def baselined_measurements(self) -> dict[str, set[TopologyFingerprint]]:
        # Mirrors chutes-ops teeMeasurements. The two fingerprints differ only in which
        # host NUMA node the four NVSwitches attach to (chassis-dependent); GPUs always
        # 4+4. No flat entry: no flat-path H200 is baselined at 10.2.1 (the only
        # supported QEMU), so a >2-NUMA-node H200 host is refused at launch. NOTE: a
        # 192-CPU H200 host now derives 188 vcpus (its own fingerprint) instead of being
        # pinned to 124; capture its CPU with discover-profile.sh and register that
        # RTMR0 before running one.
        return {"10.2.1": {known.H200_KR6288, known.H200_XE9680}}

    def describe_mode(self, total_gpus: int) -> str:
        if total_gpus == 8:
            return "PPCIe mode (8 GPUs, H200)"
        return "CC mode (H200)"


class RTXPro6000Profile(GpuProfile):
    # 2bb1 = Workstation Edition, 2bb5 = Server Edition
    pci_device_ids = ["2bb1", "2bb5"]
    display_name = "8xpro_6000"
    expected_gpus = ["pro_6000"]
    # lspci -vvvnn on box-028 (10de:2bb5, Server Edition): BAR2 resizable, current 128GB.
    # Stub id 2bb1 (pci_device_ids[0]); the device id is measurement-neutral.
    passthrough = {
        "gpu": PassthroughDevice(
            0x10DE,
            "2bb1",
            0x0302,
            [PciBar(0, 64, "p64"), PciBar(2, 131072, "p64"), PciBar(4, 32, "p64")],
        ),
    }

    @property
    def name(self) -> str:
        return "RTX_PRO_6000"

    @property
    def bar_size_mb(self) -> int:
        # 128 GiB: matches lspci "Physical Resizable BAR / BAR 2: current size: 128GB" on 2bb5 Server Edition.
        return 131072

    @property
    def vram_gb(self) -> int:
        return 96  # GDDR7

    # Host: 2 sockets × 64 cores × 1 thread = 128 Intel Xeon (Sierra Forest E-core,
    # no SMT) → 124 vcpus. From discover-profile.sh on eu1-hpe1-rtx6000pro-se-008.

    @property
    def enable_numa_topology(self) -> bool:
        # Host has 2 NUMA nodes with GPUs split 4+4 across sockets.
        # Confirmed from discover-profile.sh on eu1-hpe1-rtx6000pro-se-001.
        return True

    @property
    def enable_post_launch_tuning(self) -> bool:
        return True

    def get_cc_mode_args(self, total_gpus: int) -> list[list[str]]:
        return [["--set-cc-mode=on", "--reset-after-cc-mode-switch"]]

    def should_passthrough_nvswitches(self, total_gpus: int) -> bool:
        return False

    @property
    def baselined_measurements(self) -> dict[str, set[TopologyFingerprint]]:
        # Two host shapes distinguished purely by NUMA node count: 2 nodes → guest-NUMA
        # path (GPUs 4+4); >2 nodes → flat fallback (only GPU count matters). Intel Xeon
        # host, CPU captured (see known.RTX_XEON). QEMU 10.2.1 = Ubuntu 26.04.
        return {"10.2.1": {known.RTX_NUMA, known.RTX_FLAT}}

    def describe_mode(self, total_gpus: int) -> str:
        return "CC mode (RTX Pro 6000)"


GPU_PROFILES: dict[str, GpuProfile] = {
    "B200": B200Profile(),
    "B300": B300Profile(),
    "H200": H200Profile(),
    "RTX_PRO_6000": RTXPro6000Profile(),
}


def resolve_profile(gpu_models: dict[str, str]) -> GpuProfile:
    """Resolve a single GpuProfile from detected GPU models.

    All GPUs must be the same supported model. Raises ValueError on mixed
    or unsupported types.
    """
    model_names = set(gpu_models.values()) - {"default"}
    if not model_names:
        raise ValueError(
            "No supported GPU models detected. "
            f"Found models: {set(gpu_models.values())}. "
            f"Supported: {list(GPU_PROFILES.keys())}"
        )
    if len(model_names) > 1:
        raise ValueError(
            f"Mixed GPU models detected: {model_names}. "
            "All GPUs must be the same model."
        )
    model = model_names.pop()
    profile = GPU_PROFILES.get(model)
    if profile is None:
        raise ValueError(
            f"Unsupported GPU model: {model}. "
            f"Supported: {list(GPU_PROFILES.keys())}"
        )
    return profile
