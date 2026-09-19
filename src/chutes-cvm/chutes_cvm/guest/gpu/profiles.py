"""GPU profile registry: per-GPU-type passthrough behavior.

Each supported GPU model is a GpuProfile subclass that encodes BAR sizes,
CC/PPCIe mode configuration, NVSwitch policy, and InfiniBand policy.
Adding a new GPU type requires one subclass and one GPU_PROFILES entry.

## Adding a new GPU profile

A GpuProfile holds only GPU-MODEL policy that is identical on every host the GPU
ships on. The host-instance facts that feed RTMR0 — the guest -smp (vcpus +
sockets), guest RAM, and CPU identity (vendor + SMBIOS Type-4 Processor ID) — are
NOT profile constants: they live on the topology fingerprint (gpu/topology.py),
detected from the live host. So "same GPU, different CPU/RAM host" is two
fingerprints of one profile, not two profiles (e.g. B200 on a 192-CPU Xeon vs a
288-CPU Xeon 6). The set of known host classes and their acceptance is owned by the
API, not this repo — ``chutes-cvm measurements generate`` derives each class's
fingerprint from the host profiles the API publishes.

To add a profile:
  1. Encode GPU-model policy on the subclass: pci_device_id, BAR/VRAM, CC/PPCIe
     mode, NVSwitch/IB policy, firmware. Override ``host_reserved_cpus`` if the
     host runs a heavy fixed workload (B200 = 16 for FabricManager; default 4).
     Keep host_reserved_cpus EVEN so vcpus divides across sockets. Guest RAM needs
     no override -- ``vram_gb`` is its whole input.
  2. Submit each host CLASS the GPU ships on via ``chutes-cvm host submit-profile``
     (``discover-profile.sh`` captures the CPU identity and the per-device BARs the
     fingerprint is built from). The API records it and returns the
     fingerprint the measurement generator then builds against — no per-class data
     is hardcoded here.

Changing host_reserved_cpus / vram_gb moves the guest's vcpus/mem → RTMR0, so it
requires re-baselining that profile's attestation policy.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from chutes_cvm.guest.devices import PciBar

HOST_RESERVED_CPUS = 4


@dataclass
class PassthroughDevice:
    """A passthrough endpoint reproduced offline as a ``pci-bar-stub``: its PCI vendor,
    device id, class, and BAR layout, all from ``lspci -vvvnn``. Keyed by endpoint kind
    ("gpu"/"nvswitch"/"ib") the endpoint hangs off.
    """

    vendor: int  # e.g. 0x10DE (NVIDIA), 0x15B3 (Mellanox / IB)
    device_id: str  # hex, e.g. "22a3"
    pci_class: int  # e.g. 0x0680
    bars: list[PciBar]


class GpuProfile(ABC):
    """Base class for GPU-type-specific passthrough behavior."""

    # The PCI device ID that identifies this GPU (e.g. 10de:2901 -> 2901). Override in subclass.
    # Drives profile DETECTION at launch (matches_device_id); the offline stub id for the
    # Must name the device the host reports for this model.
    #
    # Exactly one per profile, deliberately. A profile is not just a BAR layout: it carries
    # host_reserved_cpus, vram_gb, firmware_filename, expected_gpus
    # and the CC/PPCIe mode arguments. Two products that happen to agree on those today can
    # diverge later with nothing to notice, so distinct hardware gets a distinct profile. The
    # control plane already models it that way -- its host-class fingerprint includes the
    # device-id set, so two editions of a card are already two classes there.
    pci_device_id: str = ""

    # Human-facing hardware identity for the generated teeMeasurements entry
    # (e.g. "8xh200"), combined with the QEMU version + the topology's computed
    # variant_label to form each hardware `name`. expected_gpus is the GPU-type
    # id(s) surfaced in that entry. Override both per subclass.
    display_name: str = ""
    expected_gpus: list[str] = []

    def matches_device_id(self, device_id: str) -> bool:
        """Return True if device_id is this profile's GPU."""
        return device_id.lower() == self.pci_device_id.lower()

    @property
    @abstractmethod
    def name(self) -> str:
        """Short model identifier (e.g. 'B200', 'H200')."""
        ...

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
    # host_cpus − host_reserved_cpus; sockets; mem from vram_gb; CPU via
    # /proc/cpuinfo); acceptance of the resulting fingerprint is the API's call. The
    # profile supplies only host_reserved_cpus and vram_gb.

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
    profile, not two profiles. 2 NUMA nodes with GPUs split 4+4 across sockets.
    Confirmed from discover-profile.sh on am-b200-57 (Xeon) and chutes-miner-gpu-0 (Xeon 6).
    """

    pci_device_id = "2901"
    display_name = "8xb200"
    expected_gpus = ["b200"]

    @property
    def name(self) -> str:
        return "B200"

    @property
    def vram_gb(self) -> int:
        return 192  # B200 HBM3e

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
    def enable_post_launch_tuning(self) -> bool:
        return True

    @property
    def requires_fabric_manager(self) -> bool:
        return True

    def describe_mode(self, total_gpus: int) -> str:
        return "CC mode (B200)"


class B300Profile(GpuProfile):
    pci_device_id = "3182"  # GB110 [B300 SXM6 AC]
    display_name = "8xb300"
    expected_gpus = ["b300"]

    @property
    def name(self) -> str:
        return "B300"

    @property
    def vram_gb(self) -> int:
        return 288  # B300 HBM3e (SXM6 AC)

    # Host: 2 sockets x 48 cores x 2 threads = 192 (Intel, from lscpu on am-b300-61)
    # → 188 vcpus. Not yet submitted to the API (uncharacterized): run
    # `chutes-cvm host submit-profile` on a B300 host so its class gets a fingerprint.

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

    def describe_mode(self, total_gpus: int) -> str:
        return "CC mode (B300)"

    @property
    def requires_fabric_manager(self) -> bool:
        return True


class H200Profile(GpuProfile):
    pci_device_id = "2335"  # H200 SXM (GH100)
    display_name = "8xh200"
    expected_gpus = ["h200"]

    @property
    def name(self) -> str:
        return "H200"

    @property
    def vram_gb(self) -> int:
        return 141  # H200 HBM3e

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

    def describe_mode(self, total_gpus: int) -> str:
        if total_gpus == 8:
            return "PPCIe mode (8 GPUs, H200)"
        return "CC mode (H200)"


class RTXPro6000Profile(GpuProfile):
    pci_device_id = "2bb5"
    display_name = "8xpro_6000"
    expected_gpus = ["pro_6000"]

    @property
    def name(self) -> str:
        return "RTX_PRO_6000"

    @property
    def vram_gb(self) -> int:
        return 96  # GDDR7

    # Host: 2 sockets × 64 cores × 1 thread = 128 Intel Xeon (Sierra Forest E-core,
    # no SMT) → 124 vcpus. From discover-profile.sh on eu1-hpe1-rtx6000pro-se-008.

    @property
    def enable_post_launch_tuning(self) -> bool:
        return True

    def get_cc_mode_args(self, total_gpus: int) -> list[list[str]]:
        return [["--set-cc-mode=on", "--reset-after-cc-mode-switch"]]

    def should_passthrough_nvswitches(self, total_gpus: int) -> bool:
        return False

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
