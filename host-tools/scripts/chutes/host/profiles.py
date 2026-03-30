"""Host profile registry: per-Ubuntu-version TDX host setup parameters.

Each supported Ubuntu version is a HostProfile subclass that declares PPAs,
kernel package, apt packages, and GRUB cmdline additions. A single setup
orchestrator consumes the profile — no OS-version branching in the setup logic.
Adding a new Ubuntu version requires one subclass and one HOST_PROFILES entry.
"""

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PPA:
    """APT PPA descriptor with pinning priority."""

    team: str
    name: str
    pin_priority: int = 4000

    @property
    def uri(self) -> str:
        return f"ppa:{self.team}/{self.name}"


class HostProfile(ABC):
    """Base class for Ubuntu-version-specific TDX host setup."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Ubuntu version string (e.g. '25.04')."""
        ...

    @property
    @abstractmethod
    def codename(self) -> str:
        """Ubuntu release codename (e.g. 'plucky')."""
        ...

    @property
    def ppas(self) -> list[PPA]:
        """PPAs to add before installing packages."""
        return []

    @property
    @abstractmethod
    def kernel_package(self) -> str:
        """Metapackage for the TDX-capable kernel."""
        ...

    @property
    @abstractmethod
    def packages(self) -> list[str]:
        """All apt packages to install (QEMU, libvirt, attestation, etc.)."""
        ...

    @property
    def grub_cmdline_additions(self) -> list[str]:
        """Extra kernel parameters for GRUB_CMDLINE_LINUX_DEFAULT."""
        return ["nohibernate"]

    def describe(self) -> str:
        """Human-readable summary for logging."""
        return f"Ubuntu {self.name} ({self.codename})"


class Ubuntu2504Profile(HostProfile):
    """Ubuntu 25.04 (Plucky) — TDX via kobuk-team PPA, used for H200 hosts."""

    @property
    def name(self) -> str:
        return "25.04"

    @property
    def codename(self) -> str:
        return "plucky"

    @property
    def ppas(self) -> list[PPA]:
        return [
            PPA("kobuk-team", "tdx-release"),
            PPA("kobuk-team", "tdx-attestation-release"),
        ]

    @property
    def kernel_package(self) -> str:
        return "linux-image-intel"

    @property
    def packages(self) -> list[str]:
        return [
            "qemu-system-x86",
            "libvirt-clients",
            "sgx-dcap-pccs",
            "tdx-qgs",
            "libsgx-dcap-default-qpl",
            "sgx-ra-service",
            "sgx-pck-id-retrieval-tool",
        ]

    @property
    def grub_cmdline_additions(self) -> list[str]:
        return ["nohibernate"]


class Ubuntu2510Profile(HostProfile):
    """Ubuntu 25.10 (Questing) — native TDX kernel, attestation via PPA."""

    @property
    def name(self) -> str:
        return "25.10"

    @property
    def codename(self) -> str:
        return "questing"

    @property
    def ppas(self) -> list[PPA]:
        # TDX kernel/QEMU are native in 25.10; only attestation needs PPA
        # (not yet in standard repos per https://launchpad.net/bugs/2131022)
        return [
            PPA("kobuk-team", "tdx-attestation-release"),
        ]

    @property
    def kernel_package(self) -> str:
        return "linux-image-generic"

    @property
    def packages(self) -> list[str]:
        return [
            "qemu-system-x86",
            "libvirt-clients",
            "sgx-dcap-pccs",
            "tdx-qgs",
            "libsgx-dcap-default-qpl",
            "sgx-ra-service",
            "sgx-pck-id-retrieval-tool",
        ]

    @property
    def grub_cmdline_additions(self) -> list[str]:
        # kvm_intel.tdx=1 required per https://bugs.launchpad.net/ubuntu/+source/linux/+bug/2121873
        return ["nohibernate", "kvm_intel.tdx=1"]


HOST_PROFILES: dict[str, HostProfile] = {
    "25.04": Ubuntu2504Profile(),
    "25.10": Ubuntu2510Profile(),
}


def detect_ubuntu_version() -> str:
    """Detect the running Ubuntu version via lsb_release."""
    result = subprocess.run(
        ["lsb_release", "-rs"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to detect Ubuntu version via lsb_release. "
            "Is this an Ubuntu system?"
        )
    return result.stdout.strip()


def resolve_profile(version: str | None = None) -> HostProfile:
    """Resolve a HostProfile for the given (or detected) Ubuntu version.

    Raises ValueError if the version is not supported.
    """
    if version is None:
        version = detect_ubuntu_version()

    profile = HOST_PROFILES.get(version)
    if profile is None:
        raise ValueError(
            f"Unsupported Ubuntu version: {version}. "
            f"Supported: {list(HOST_PROFILES.keys())}"
        )
    return profile
