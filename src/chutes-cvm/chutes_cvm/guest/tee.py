"""Host-side TEE abstraction for the guest QEMU command.

The same launcher runs on Intel TDX and AMD SEV-SNP hosts, so the handful of
QEMU arguments that actually differ between them live here rather than being
branched inline. Everything else in ``QemuCommand`` — drives, netdevs, PCIe
topology, direct boot — is identical across both platforms.

What genuinely differs:

* the confidential-guest object and the id the machine references
* the memory backend: TDX uses ``memory-backend-ram``; SNP requires
  ``memory-backend-memfd`` with ``share=on`` because private memory is served
  from guest_memfd
* ``vmport`` is disabled on SNP per NVIDIA's confidential-computing guide
* SNP takes the C-bit position; TDX has no equivalent
* SNP has no quote-generation socket — the PSP answers guest requests directly,
  so there is no host daemon to reach over vsock
"""

import os
import struct
from abc import ABC, abstractmethod
from enum import Enum

KVM_INTEL_TDX = "/sys/module/kvm_intel/parameters/tdx"
KVM_AMD_SEV_SNP = "/sys/module/kvm_amd/parameters/sev_snp"

CPUID_DEVICE = "/dev/cpu/0/cpuid"
# CPUID Fn8000_001F: EBX[5:0] = C-bit position, EBX[11:6] = phys addr bits lost.
SEV_CPUID_LEAF = 0x8000001F
# Every SEV-capable EPYC to date places the C-bit at 51 and loses one physical
# address bit. Used only when /dev/cpu/0/cpuid is unavailable; QEMU validates
# the value against the host and fails loudly with the real one if it is wrong,
# so a stale default cannot silently weaken anything.
DEFAULT_CBITPOS = 51
DEFAULT_REDUCED_PHYS_BITS = 1


class HostTee(str, Enum):
    """Confidential-computing platform the host provides."""

    TDX = "tdx"
    SNP = "snp"


def _module_param_enabled(path: str) -> bool:
    """True when a kvm module parameter reads as enabled ('Y' or '1')."""
    try:
        with open(path) as f:
            return f.read().strip().upper() in ("Y", "1")
    except OSError:
        return False


def detect_host_tee(override: "HostTee | str | None" = None) -> HostTee:
    """Determine which TEE this host can launch guests under.

    Detection reads the kvm module parameters rather than the CPU vendor: a
    Genoa host with SEV-SNP disabled in BIOS reports AMD but cannot launch an
    SNP guest, and failing here is far clearer than failing inside QEMU.
    """
    if override is not None:
        return HostTee(override)

    if _module_param_enabled(KVM_INTEL_TDX):
        return HostTee.TDX

    if _module_param_enabled(KVM_AMD_SEV_SNP):
        return HostTee.SNP

    raise RuntimeError(
        "No confidential-computing platform is enabled on this host: neither "
        f"{KVM_INTEL_TDX} nor {KVM_AMD_SEV_SNP} reads as enabled. Whether a platform is "
        "on is a BIOS setting, not a property of the silicon; the per-platform remedy "
        "is on the provider (TeeProvider.verify_environment)."
    )


def sev_cbit_parameters() -> "tuple[int, int]":
    """Return ``(cbitpos, reduced_phys_bits)`` for this host.

    Read from CPUID Fn8000_001F via /dev/cpu/0/cpuid when the cpuid module is
    loaded, falling back to the documented EPYC defaults otherwise.
    """
    try:
        with open(CPUID_DEVICE, "rb") as f:
            f.seek(SEV_CPUID_LEAF * 16)
            raw = f.read(16)
        if len(raw) == 16:
            _, ebx, _, _ = struct.unpack("<IIII", raw)
            cbitpos = ebx & 0x3F
            reduced = (ebx >> 6) & 0x3F
            if cbitpos:
                return cbitpos, reduced
    except OSError:
        pass
    return DEFAULT_CBITPOS, DEFAULT_REDUCED_PHYS_BITS


class TeeProvider(ABC):
    """Per-platform QEMU arguments for launching a confidential guest."""

    # Annotated without defaults on purpose: a subclass that forgets one raises
    # AttributeError at first use, instead of silently emitting an empty value
    # (``confidential-guest-support=``, a zero-length firmware path) that fails far
    # from its cause.

    #: Value used in the -machine confidential-guest-support= reference.
    guest_id: str

    #: SMBIOS product string. Pinned per platform so per-server motherboard
    #: differences don't shift measurements within a profile.
    smbios_product: str

    #: Firmware image this platform boots.
    default_firmware: str

    #: Operator-facing platform name.
    label: str

    #: QEMU object type backing guest RAM. SEV-SNP serves private memory from
    #: guest_memfd, where memory-backend-ram simply fails; TDX uses plain ram. That is
    #: the ONLY way the platform affects the backend, so it is a value rather than a
    #: method each subclass reimplements -- the NUMA binding below is identical for both.
    memory_backend_type: str

    #: Extra backend options the platform requires (SNP needs share=on).
    memory_backend_opts: "tuple[str, ...]" = ()

    #: kvm module parameter that reports this platform enabled on the running host.
    kvm_param: str

    #: What to do about it when that parameter reads disabled. Per-platform because the
    #: remedy is: the BIOS settings differ, and a message naming the wrong vendor's
    #: switches is worse than none.
    enablement_hint: str

    @abstractmethod
    def guest_object(self) -> str:
        """The ``-object`` argument declaring the confidential guest."""
        ...

    def verify_environment(self) -> None:
        """Raise unless THIS provider's platform is switched on for this host.

        The capability question, and deliberately not the identity one. The profile says
        which platform a host class runs (CPU vendor, see ``provider_for_cpu_vendor``);
        only the live kvm module parameter says whether the machine in front of you has
        it turned on. SEV-SNP can be off in BIOS on AMD silicon, and a mismatch equally
        catches a profile captured on different hardware than the one booting it --
        either way the guest would be measured against a platform it is not running on.

        The provider owns this because the parameter and the remedy are both
        platform-specific, exactly as the firmware and the guest object are.
        """
        if _module_param_enabled(self.kvm_param):
            return
        try:
            detail = f"{detect_host_tee().value} enabled instead"
        except RuntimeError:
            detail = "no confidential-computing platform enabled at all"
        raise RuntimeError(
            f"This host profile is {self.label} ({self.guest_id}), but the machine "
            f"reports {detail} ({self.kvm_param} does not read as enabled). "
            f"{self.enablement_hint}"
        )

    def memory_backend(
        self,
        backend_id: str,
        size: str,
        host_node: "int | None" = None,
    ) -> str:
        """A ``-object`` memory backend of the type this platform requires.

        NB: never set ``prealloc=on``. Guest RAM is private memory served from
        guest_memfd and allocated as the guest accepts pages; preallocating
        pins a second full copy the guest never uses, roughly doubling host
        memory use and OOM-killing the host during pod warmup.
        """
        parts = [self.memory_backend_type, f"id={backend_id}", f"size={size}"]
        parts.extend(self.memory_backend_opts)
        if host_node is not None:
            parts += [f"host-nodes={host_node}", "policy=bind"]
        return ",".join(parts)

    def machine(self, memory_backend: "str | None") -> str:
        """The ``-machine`` argument, optionally bound to a flat memory backend."""
        machine = f"q35,kernel_irqchip=split,confidential-guest-support={self.guest_id}"
        machine += self._machine_extra()
        if memory_backend:
            machine += f",memory-backend={memory_backend}"
        return machine

    def _machine_extra(self) -> str:
        return ""


class TdxTeeProvider(TeeProvider):
    """Intel TDX."""

    guest_id = "tdx"
    smbios_product = "TDX-VM"
    default_firmware = "OVMF.inteltdx.fd"
    label = "Intel TDX"
    memory_backend_type = "memory-backend-ram"
    kvm_param = KVM_INTEL_TDX
    enablement_hint = (
        "Either the profile was captured on other hardware, or Intel TDX is not "
        "enabled in BIOS on this host."
    )

    def guest_object(self) -> str:
        # The quote-generation socket reaches qgsd on the host over vsock; TDX
        # quotes are produced outside the guest, unlike SNP reports.
        return (
            '{"qom-type":"tdx-guest","id":"tdx",'
            '"quote-generation-socket":{"type":"vsock","cid":"2","port":"4050"}}'
        )


def tee_for_cpu_vendor(cpu_vendor: str) -> str:
    """Which TEE a hardware class runs, from its CPUID vendor string.

    A hardware class is one vendor's silicon or the other's, never both, so the vendor
    recorded on its fingerprint is the discriminator -- there is no separate field to
    keep in sync. Used by offline measurement generation to decide which measurement a
    given host profile needs.
    """
    vendor = (cpu_vendor or "").strip()
    if vendor == "AuthenticAMD":
        return "snp"
    if vendor == "GenuineIntel":
        return "tdx"
    raise ValueError(
        f"cannot determine the TEE for CPU vendor {cpu_vendor!r}; expected "
        "GenuineIntel (TDX) or AuthenticAMD (SEV-SNP)"
    )


# The SEV-SNP guest policy bits the launcher sets. Part of the launch measurement
# input set, so offline generation imports this rather than restating the value --
# a policy that drifts from the measured one produces a VM that cannot attest.
#   bit 16 SMT allowed, bit 17 reserved (must be 1). DEBUG (19) deliberately clear.
SNP_DEFAULT_POLICY = 0x30000


class SnpTeeProvider(TeeProvider):
    """AMD SEV-SNP."""

    guest_id = "snp0"
    smbios_product = "SNP-VM"
    default_firmware = "OVMF.amdsev.fd"
    label = "AMD SEV-SNP"
    memory_backend_type = "memory-backend-memfd"
    memory_backend_opts = ("share=on",)
    kvm_param = KVM_AMD_SEV_SNP
    enablement_hint = (
        "Either the profile was captured on other hardware, or SEV-SNP is not enabled "
        "in BIOS — check that SEV-SNP Support and SMEE are on, SEV-ES ASID Space Limit "
        "is non-trivial (1 leaves zero usable SNP ASIDs), and TSME is off."
    )

    def __init__(
        self,
        cbitpos: "int | None" = None,
        reduced_phys_bits: "int | None" = None,
        policy: int = SNP_DEFAULT_POLICY,
        kernel_hashes: bool = True,
    ):
        detected_cbitpos, detected_reduced = sev_cbit_parameters()
        self.cbitpos = cbitpos if cbitpos is not None else detected_cbitpos
        self.reduced_phys_bits = (
            reduced_phys_bits if reduced_phys_bits is not None else detected_reduced
        )
        # Bits 16 (SMT allowed) and 17 (reserved, must be 1). Bit 19 (DEBUG) is
        # deliberately clear: with it set the host can decrypt and inspect guest
        # memory, and the report would still carry a valid signature.
        self.policy = policy
        # Puts SHA-256 of kernel/initrd/cmdline in a measured page, which OVMF
        # then enforces before boot. This is what makes the measured-initrd key
        # release gate work on SNP, so it is not optional.
        self.kernel_hashes = kernel_hashes

    def guest_object(self) -> str:
        obj = (
            f"sev-snp-guest,id={self.guest_id},"
            f"cbitpos={self.cbitpos},"
            f"reduced-phys-bits={self.reduced_phys_bits},"
            f"policy={self.policy:#x}"
        )
        if self.kernel_hashes:
            obj += ",kernel-hashes=on"
        return obj

    def _machine_extra(self) -> str:
        # NVIDIA's confidential-computing deployment guide specifies vmport=off
        # for SEV-SNP guests.
        return ",vmport=off"


_PROVIDERS = {
    HostTee.TDX: TdxTeeProvider,
    HostTee.SNP: SnpTeeProvider,
}


def provider_for_cpu_vendor(cpu_vendor: str) -> TeeProvider:
    """The TEE provider a host class runs, from its CPU vendor.

    The identity question, and the one a host profile answers: this silicon runs that
    TEE. Distinct from ``detect_host_tee``, which answers the capability question --
    whether the platform is switched on right now -- and cannot be derived from a
    profile because it is a BIOS setting, not a property of the class.
    """
    return _PROVIDERS[HostTee(tee_for_cpu_vendor(cpu_vendor))]()


def tee_firmware_available(provider: TeeProvider, firmware_dir: str) -> bool:
    """True when this platform's firmware image is present in ``firmware_dir``."""
    return os.path.exists(os.path.join(firmware_dir, provider.default_firmware))
