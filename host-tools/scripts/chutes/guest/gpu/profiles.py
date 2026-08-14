"""GPU profile registry: per-GPU-type passthrough behavior.

Each supported GPU model is a GpuProfile subclass that encodes BAR sizes,
CC/PPCIe mode configuration, NVSwitch policy, and InfiniBand policy.
Adding a new GPU type requires one subclass and one GPU_PROFILES entry.

## Adding a new GPU profile

Before writing a subclass, run the following on the bare-metal host to
determine the correct CPU topology values:

    lscpu | grep -E "Socket|Core\\(s\\) per|Thread|NUMA node\\(s\\)|CPU\\(s\\):"

Example output for a 2-socket Xeon system:

    CPU(s):                    128
    Thread(s) per core:        2
    Core(s) per socket:        32
    Socket(s):                 2
    NUMA node(s):              2

Map these to the profile properties:

    host_cpus    = CPU(s)                            → 128
    host_sockets = Socket(s)                         → 2
    vcpus        = host_cpus - host_reserved_cpus    → 124  (derived, no override needed)
    smp_topology = derived automatically from the above (no override needed)

host_reserved_cpus is the number of logical CPUs kept for the host OS. It
defaults to HOST_RESERVED_CPUS (4) and is a per-profile property so a GPU type
with a heavier host workload can reserve more without shifting the vcpu count —
and therefore RTMR0 — of unrelated profiles. B200/B200_XEON6 override it to 16
because the host runs FabricManager alongside QEMU's iothreads and, under heavy
NVLink/NCCL I/O, a thin reserve starves those threads (observed as
cudaErrorNvlinkUncorrectable in the guest).

Keep any override EVEN: vcpus must divide across host_sockets (2) for a clean
-smp topology. Changing host_reserved_cpus changes vcpus → smp_topology →
RTMR0, so any change requires re-baselining that profile's attestation policy.
vcpus and smp_topology are otherwise computed automatically — only override
host_cpus/host_sockets if the server has a non-standard layout.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from chutes.guest.gpu.topology import FlatTopology, NumaTopology, TopologyFingerprint

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
    """A passthrough endpoint type reproduced offline as a ``pci-bar-stub``: its PCI device
    id + class + BAR layout, all from ``lspci -vvvnn``. Keyed by endpoint kind (e.g.
    ``"nvswitch"``) in ``GpuProfile.passthrough``.
    """

    device_id: str        # hex, e.g. "22a3"
    pci_class: int        # e.g. 0x0680
    bars: list[PciBar]


class GpuProfile(ABC):
    """Base class for GPU-type-specific passthrough behavior."""

    # PCI device IDs that identify this GPU (e.g. [10de:2901] -> 2901). Override in subclass.
    pci_device_ids: list[str] = []

    # Full PCI BAR layout from `lspci -vvvnn` (see PciBar / discover-profile.sh).
    # Empty = not yet captured for this model; offline measurement generation is
    # unavailable until it is (the per-GPU MMIO windows can't be reproduced).
    pci_bars: list[PciBar] = []

    # Auxiliary passthrough endpoints beyond the GPU (`pci_bars` above), keyed by the
    # root-port kind _swap_endpoint matches: "nvswitch" (rp_nvsw*), "ib" (rp_ib*). Empty
    # for profiles that pass only GPUs through.
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
    @abstractmethod
    def host_cpus(self) -> int:
        """Total physical CPU count (CPU(s) from lscpu). See module docstring."""
        ...

    @property
    def host_sockets(self) -> int:
        """Physical socket count (Socket(s) from lscpu). Override per profile."""
        return 1

    @property
    def host_reserved_cpus(self) -> int:
        """Logical CPUs kept for the host OS (not handed to the guest).

        Defaults to HOST_RESERVED_CPUS. Override per profile when the host
        carries a heavier fixed workload (e.g. FabricManager on NVSwitch HGX
        systems). Must be even so vcpus divides across host_sockets. Changing
        it changes vcpus → smp_topology → RTMR0; re-baseline attestation.
        """
        return HOST_RESERVED_CPUS

    @property
    def vcpus(self) -> int:
        """vCPUs allocated to the VM (host CPUs minus reserve)."""
        return self.host_cpus - self.host_reserved_cpus

    @property
    def smp_topology(self) -> str:
        """Full QEMU -smp topology string.

        Mirrors the physical socket layout so QEMU synthesizes CPUID topology
        leaves that match the host structure. threads=1 disables SMT in the
        guest — each vCPU appears as an independent core, which produces a
        clean scheduler topology without requiring guest HT awareness.
        """
        cores_per_socket = self.vcpus // self.host_sockets
        return f"{self.vcpus},sockets={self.host_sockets},cores={cores_per_socket},threads=1"

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

    @property
    def ram_per_gpu_gb(self) -> int:
        """VM RAM allocated per GPU in GB. Defaults to vram_gb; override when
        host RAM allows more headroom than VRAM (e.g. B200 with 3 TB host RAM).
        """
        return self.vram_gb

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
    """B200 on a standard Intel Xeon host (2×48c×2t = 192 CPUs, ~2 TB RAM).

    Confirmed from discover-profile.sh on am-b200-57.
    2 NUMA nodes with GPUs split 4+4 across sockets.
    """

    pci_device_ids = ["2901"]

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

    @property
    def ram_per_gpu_gb(self) -> int:
        # Host has ~2 TB RAM (2015 GB observed); leave ~64 GB for host OS.
        # 8 GPUs → (2015 - 64) / 8 ≈ 244 GB per GPU.
        # Confirmed from discover-profile.sh on am-b200-57.
        return 243

    @property
    def host_cpus(self) -> int:
        # 2 sockets × 48 cores × 2 threads = 192.
        # Confirmed from discover-profile.sh on am-b200-57.
        return 192

    @property
    def host_sockets(self) -> int:
        return 2

    @property
    def host_reserved_cpus(self) -> int:
        # 16 logical (8 physical cores, 4/socket) → 176 vcpus, 88 cores/socket.
        # The host runs FabricManager alongside QEMU's iothreads/vhost workers;
        # the default reserve of 4 starves them under heavy NVLink/NCCL I/O,
        # surfacing as cudaErrorNvlinkUncorrectable in the guest. The reserved
        # cores also widen the gap the iothreads pin into (see post_launch.py).
        # Inherited by B200Xeon6Profile. Even, so vcpus stays socket-divisible.
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
        # Host has 2 NUMA nodes with GPUs split 4+4 across sockets.
        return True

    @property
    def enable_post_launch_tuning(self) -> bool:
        return True

    @property
    def requires_fabric_manager(self) -> bool:
        return True

    @property
    def baselined_measurements(self) -> dict[str, set[TopologyFingerprint]]:
        # No NVSwitch and no IB passthrough -> only gpu_nodes set. Every B200 maps
        # here regardless of NIC loadout. QEMU 10.2.1 (26.04).
        return {"10.2.1": {NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1))}}

    def describe_mode(self, total_gpus: int) -> str:
        return "CC mode (B200)"


class B200Xeon6Profile(B200Profile):
    """B200 on an Intel Xeon 6 host (2×72c×2t = 288 CPUs, ~3 TB RAM, SNC3).

    Same GPU and passthrough behavior as B200Profile but different host CPU
    topology. Uses Sub-NUMA Clustering (SNC3): 3 nodes per socket → 6 nodes
    total. use_numa_topology() requires exactly 2 nodes, so enable_numa_topology
    has no effect on current SNC3 hardware and falls back to numactl --interleave.
    Flag kept True so it activates automatically when SNC3 support is added.

    Confirmed from discover-profile.sh on chutes-miner-gpu-0.

    NOTE (revisit next release): this subclass exists only because host_cpus
    (288 vs 192) and ram_per_gpu_gb (369 vs 243) differ from B200Profile — both
    are host-instance facts, not GPU-model policy. The plan is to fold vcpus (from
    the live host) and guest mem (B200 derives it from host RAM: (host_gb-64)//gpus
    — see discover-profile.sh) into the topology fingerprint and collapse this into
    a single B200Profile, so "B200 vs Xeon6" becomes two fingerprints rather than
    two classes. Deferred now because it would move RTMR0 for off-nominal hosts
    (e.g. a 192-CPU/3 TB B200 currently pinned to mem=1944 would derive 2952); once
    the next-release flow captures+validates+reports topology, updating those
    measurements is cheap. See gpu/topology.py.
    """

    pci_device_ids = ["2901"]

    @property
    def name(self) -> str:
        return "B200_XEON6"

    @property
    def ram_per_gpu_gb(self) -> int:
        # Host has ~3 TB RAM (3022 GB observed); leave ~64 GB for host OS.
        # 8 GPUs → (3022 - 64) / 8 ≈ 369 GB per GPU.
        return 369

    @property
    def host_cpus(self) -> int:
        # 2 sockets × 72 cores × 2 threads = 288.
        # Confirmed from discover-profile.sh on chutes-miner-gpu-0.
        return 288

    @property
    def baselined_measurements(self) -> dict[str, set[TopologyFingerprint]]:
        return {
            # gd-251: SNC off -> 2 NUMA nodes -> NUMA path, GPUs 4+4.
            "10.2.1": {NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1))},
            # Xeon6 SNC3 -> 6 nodes -> flat fallback.
            "10.1.0": {FlatTopology(gpu_count=8)},
        }

    def describe_mode(self, total_gpus: int) -> str:
        return "CC mode (B200 Xeon6)"


class B300Profile(GpuProfile):
    pci_device_ids = ["3182"]  # GB110 [B300 SXM6 AC]

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

    @property
    def host_cpus(self) -> int:
        # 2 sockets x 48 cores x 2 threads = 192 (confirmed from lscpu on am-b300-61).
        return 192

    @property
    def host_sockets(self) -> int:
        return 2

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
    # lspci -vvvnn on dev-h200-tee (10de:2335): BAR2 resizable, current 256GB.
    pci_bars = [
        PciBar(0, 16, "p64"),
        PciBar(2, 262144, "p64"),  # 256G VRAM
        PciBar(4, 32, "p64"),
    ]
    # NVSwitch (10de:22a3, class 0680): single 32M m64 BAR (lspci -vvvnn on dev-h200-tee).
    passthrough = {"nvswitch": PassthroughDevice("22a3", 0x0680, [PciBar(0, 32, "m64")])}

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
    def host_cpus(self) -> int:
        # 2 sockets × 32 cores × 2 threads = 128.
        # Confirmed from discover-profile.sh on dev-h200-tee.
        #
        # NOTE (revisit next release): this is pinned at 128, so EVERY H200 host
        # attests at vcpus=124 regardless of its real CPU count. The 192-CPU H200
        # hosts (e.g. h200-ar6, h200-gd-245) therefore run with 124 vcpus — 68
        # physical cores unused — and match the single 124-vcpu H200 measurement.
        # The intended fix (aligned with the B200 direction) is to derive vcpus
        # from the live host and carry the resulting -smp in the topology
        # fingerprint, so a 192-CPU H200 runs 188 vcpus with its own baseline.
        # Deferred here to avoid re-baselining those hosts mid-stream; when it
        # lands, register the 192-CPU H200 RTMR0 in chutes-ops teeMeasurements
        # first. See gpu/topology.py for the fingerprint the smp/mem would join.
        return 128

    @property
    def host_sockets(self) -> int:
        return 2

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
        # No IB passthrough -> ib_nodes empty. Mirrors chutes-ops teeMeasurements.
        # The two NUMA fingerprints differ only in which host NUMA node the four
        # NVSwitches attach to (chassis-dependent); GPUs are always 4+4.
        nvswitch_on_node1 = NumaTopology(  # e.g. Dell XE9680
            gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1), nvswitch_nodes=(1, 1, 1, 1)
        )
        nvswitch_on_node0 = NumaTopology(  # e.g. KR6288
            gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1), nvswitch_nodes=(0, 0, 0, 0)
        )
        return {
            # No 10.2.1 flat entry (no flat-path H200 baselined at 10.2.1 yet).
            "10.1.0": {
                nvswitch_on_node1,
                nvswitch_on_node0,
                FlatTopology(gpu_count=8, nvswitch_count=4),
            },
            "10.2.1": {nvswitch_on_node1, nvswitch_on_node0},
        }

    def describe_mode(self, total_gpus: int) -> str:
        if total_gpus == 8:
            return "PPCIe mode (8 GPUs, H200)"
        return "CC mode (H200)"


class RTXPro6000Profile(GpuProfile):
    # 2bb1 = Workstation Edition, 2bb5 = Server Edition
    pci_device_ids = ["2bb1", "2bb5"]
    # lspci -vvvnn on box-028 (10de:2bb5, Server Edition): BAR2 resizable, current 128GB.
    pci_bars = [
        PciBar(0, 64, "p64"),
        PciBar(2, 131072, "p64"),  # 128G VRAM
        PciBar(4, 32, "p64"),
    ]

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

    @property
    def host_cpus(self) -> int:
        # 2 sockets × 64 cores × 1 thread = 128 (AMD EPYC Genoa, no SMT).
        # Confirmed from discover-profile.sh on eu1-hpe1-rtx6000pro-se-001.
        return 128

    @property
    def host_sockets(self) -> int:
        return 2

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
        # No NVSwitch/IB -> only gpu_nodes / gpu_count set. Two host shapes,
        # distinguished purely by NUMA node count:
        #   - 2 NUMA nodes -> guest-NUMA path, GPUs 4+4.
        #   - >2 NUMA nodes (e.g. 4) -> flat fallback; only GPU count matters.
        # QEMU 10.2.1 = Ubuntu 26.04 (confirmed by discover-profile); the 10.1.0
        # entry covers RTX hosts still on 25.10.
        return {
            "10.1.0": {NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1))},
            "10.2.1": {
                NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1)),
                FlatTopology(gpu_count=8),
            },
        }

    def describe_mode(self, total_gpus: int) -> str:
        return "CC mode (RTX Pro 6000)"


GPU_PROFILES: dict[str, GpuProfile] = {
    "B200": B200Profile(),
    "B200_XEON6": B200Xeon6Profile(),
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
