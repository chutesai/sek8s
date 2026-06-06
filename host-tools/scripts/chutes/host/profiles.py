"""Host profile registry: per-Ubuntu-version TDX host setup parameters.

Each supported Ubuntu version is a HostProfile subclass that declares PPAs,
third-party APT repos, kernel package, apt packages, and GRUB cmdline additions.
A single setup orchestrator consumes the profile — no OS-version branching in the
setup logic.  Adding a new Ubuntu version requires one subclass and one
HOST_PROFILES entry.
"""

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PPA:
    """Launchpad PPA descriptor with pinning priority.

    When suite is set, the PPA sources entry uses that suite instead of the
    host codename.  This is needed when a PPA hasn't published packages for
    the running release.
    """

    team: str
    name: str
    signing_key: str
    pin_priority: int = 4000
    suite: str | None = None

    @property
    def uri(self) -> str:
        return f"ppa:{self.team}/{self.name}"


@dataclass
class APTRepo:
    """Generic APT repository (non-Launchpad).

    Used for vendor repositories such as Intel's download.01.org that are
    not Launchpad PPAs.  The signing key URL is downloaded and saved to
    /etc/apt/keyrings/ before writing a DEB822 sources entry.
    """

    name: str
    uri: str
    suite: str
    components: str
    signing_key_url: str
    pin_priority: int = 4000


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
    def repos(self) -> list[APTRepo]:
        """Third-party APT repos (non-PPA) to add before installing packages."""
        return []

    @property
    @abstractmethod
    def kernel_package(self) -> str:
        """Pinned kernel image package (e.g. 'linux-image-6.17.0-35-generic').

        Must be a concrete versioned package, not a metapackage like
        linux-image-generic, so that every host in the fleet installs the
        exact same kernel.  RTMR0 measurements depend on the host kernel
        version — unpinned kernels cause attestation failures when hosts
        diverge after routine apt upgrades.
        """
        ...

    @property
    @abstractmethod
    def packages(self) -> list[str]:
        """All apt packages to install (QEMU, attestation, etc.)."""
        ...

    @property
    def grub_cmdline_additions(self) -> list[str]:
        """Extra kernel parameters for GRUB_CMDLINE_LINUX_DEFAULT."""
        return ["nohibernate"]

    def describe(self) -> str:
        """Human-readable summary for logging."""
        return f"Ubuntu {self.name} ({self.codename})"


class Ubuntu2510Profile(HostProfile):
    """Ubuntu 25.10 (Questing) — native TDX kernel, attestation via Intel DCAP repo."""

    @property
    def name(self) -> str:
        return "25.10"

    @property
    def codename(self) -> str:
        return "questing"

    @property
    def repos(self) -> list[APTRepo]:
        # TDX kernel/QEMU are native in 25.10; Intel DCAP provides attestation packages.
        # Intel has no questing suite yet; noble packages are ABI-compatible.
        return [
            APTRepo(
                name="intel-sgx",
                uri="https://download.01.org/intel-sgx/sgx_repo/ubuntu/",
                suite="noble",
                components="main",
                signing_key_url="https://download.01.org/intel-sgx/sgx_repo/ubuntu/intel-sgx-deb.key",
            ),
        ]

    @property
    def kernel_package(self) -> str:
        return "linux-image-6.17.0-35-generic"

    @property
    def packages(self) -> list[str]:
        return [
            "qemu-system-x86",
            "sgx-dcap-pccs",
            "tdx-qgs",
            "libsgx-dcap-default-qpl",
            "sgx-ra-service",
            "sgx-pck-id-retrieval-tool",
        ]

    @property
    def grub_cmdline_additions(self) -> list[str]:
        return ["nohibernate", "kvm_intel.tdx=1", "modprobe.blacklist=nouveau"]


class Ubuntu2604Profile(HostProfile):
    """Ubuntu 26.04 (Resolute) — native TDX kernel and QEMU 10.2, attestation via Intel DCAP repo."""

    @property
    def name(self) -> str:
        return "26.04"

    @property
    def codename(self) -> str:
        return "resolute"

    @property
    def ppas(self) -> list[PPA]:
        return []

    @property
    def repos(self) -> list[APTRepo]:
        # Intel official SGX/DCAP attestation repository (no Launchpad equivalent).
        # Provides sgx-dcap-pccs, tdx-qgs, libsgx-dcap-default-qpl for TDX attestation.
        return [
            APTRepo(
                name="intel-sgx",
                uri="https://download.01.org/intel-sgx/sgx_repo/ubuntu/",
                suite="resolute",
                components="main",
                signing_key_url="https://download.01.org/intel-sgx/sgx_repo/ubuntu/intel-sgx-deb.key",
            ),
        ]

    @property
    def kernel_package(self) -> str:
        return "linux-image-6.17.0-35-generic"

    @property
    def packages(self) -> list[str]:
        return [
            "qemu-system-x86",
            "ovmf-inteltdx",
            "sgx-dcap-pccs",
            "tdx-qgs",
            "libsgx-dcap-default-qpl",
            "sgx-ra-service",
            "sgx-pck-id-retrieval-tool",
        ]

    @property
    def grub_cmdline_additions(self) -> list[str]:
        return ["nohibernate", "kvm_intel.tdx=1", "modprobe.blacklist=nouveau"]


HOST_PROFILES: dict[str, HostProfile] = {
    "25.10": Ubuntu2510Profile(),
    "26.04": Ubuntu2604Profile(),
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
