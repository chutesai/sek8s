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
    vcpus        = host_cpus - HOST_RESERVED_CPUS    → 124  (derived, no override needed)
    smp_topology = derived automatically from the above (no override needed)

HOST_RESERVED_CPUS (currently 4) is the number of logical CPUs kept for the
host OS. vcpus and smp_topology are computed from host_cpus and host_sockets
automatically — only override them if the server has a non-standard layout.
"""

from abc import ABC, abstractmethod

HOST_RESERVED_CPUS = 4


class GpuProfile(ABC):
    """Base class for GPU-type-specific passthrough behavior."""

    # PCI device IDs that identify this GPU (e.g. [10de:2901] -> 2901). Override in subclass.
    pci_device_ids: list[str] = []

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
        """MMIO BAR size in MB for QEMU fw_cfg hint."""
        ...

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
    def vcpus(self) -> int:
        """vCPUs allocated to the VM (host CPUs minus reserve)."""
        return self.host_cpus - HOST_RESERVED_CPUS

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

    @abstractmethod
    def should_passthrough_nvswitches(self, total_gpus: int) -> bool:
        """Whether NVSwitch devices should be detected and passed through."""
        ...

    @property
    def should_passthrough_infiniband(self) -> bool:
        """Whether InfiniBand devices should be detected and passed through."""
        return False

    def describe_mode(self, total_gpus: int) -> str:
        """Human-readable description of the mode for logging."""
        return f"{self.name} passthrough"


class B200Profile(GpuProfile):
    pci_device_ids = ["2901"]

    @property
    def name(self) -> str:
        return "B200"

    @property
    def bar_size_mb(self) -> int:
        return 524288  # 512GB recommended

    @property
    def vram_gb(self) -> int:
        return 192  # B200 HBM3e

    @property
    def host_cpus(self) -> int:
        return 112  # 2x Xeon 8570

    def get_cc_mode_args(self, total_gpus: int) -> list[list[str]]:
        return [["--set-cc-mode=on", "--reset-after-cc-mode-switch"]]

    def should_passthrough_nvswitches(self, total_gpus: int) -> bool:
        return False

    @property
    def should_passthrough_infiniband(self) -> bool:
        return True

    def describe_mode(self, total_gpus: int) -> str:
        return "CC mode (B200)"


class H200Profile(GpuProfile):
    pci_device_ids = ["2335"]  # H200 SXM (GH100)

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
        return 128

    @property
    def host_sockets(self) -> int:
        return 2

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

    def should_passthrough_nvswitches(self, total_gpus: int) -> bool:
        return total_gpus == 8

    def describe_mode(self, total_gpus: int) -> str:
        if total_gpus == 8:
            return "PPCIe mode (8 GPUs, H200)"
        return "CC mode (H200)"


class RTXPro6000Profile(GpuProfile):
    # 2bb1 = Workstation Edition, 2bb5 = Server Edition
    pci_device_ids = ["2bb1", "2bb5"]

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
        return 128

    @property
    def host_sockets(self) -> int:
        return 2

    def get_cc_mode_args(self, total_gpus: int) -> list[list[str]]:
        return [["--set-cc-mode=on", "--reset-after-cc-mode-switch"]]

    def should_passthrough_nvswitches(self, total_gpus: int) -> bool:
        return False

    def describe_mode(self, total_gpus: int) -> str:
        return "CC mode (RTX Pro 6000)"


GPU_PROFILES: dict[str, GpuProfile] = {
    "B200": B200Profile(),
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
