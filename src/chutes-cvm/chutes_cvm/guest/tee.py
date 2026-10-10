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
* SNP takes the C-bit position (captured with the host profile); TDX has no equivalent
* SNP has no quote-generation socket — the PSP answers guest requests directly,
  so there is no host daemon to reach over vsock, and no vsock device
* SNP guests run flat even on 2-node hosts, until QEMU can convert memory across
  guest_memfd backends (``supports_guest_numa``)
"""

import os
from abc import ABC, abstractmethod

KVM_INTEL_TDX = "/sys/module/kvm_intel/parameters/tdx"
KVM_AMD_SEV_SNP = "/sys/module/kvm_amd/parameters/sev_snp"

#: The machine every guest runs, launch or offline dump; the platform adds its options to it.
Q35_MACHINE = "q35,kernel_irqchip=split"


def _module_param_enabled(path: str) -> bool:
    """True when a kvm module parameter reads as enabled ('Y' or '1')."""
    try:
        with open(path) as f:
            return f.read().strip().upper() in ("Y", "1")
    except OSError:
        return False


class TeeProvider(ABC):
    """Per-platform QEMU arguments for launching a confidential guest.

    The provider class IS the platform: a host is Intel or AMD silicon, and each subclass declares
    the CPU vendor that runs it. Nothing else names the platform -- a host profile's platform is its
    provider, and host setup keys its recipes on the provider class.
    """

    #: The CPUID vendor string of the silicon that runs this platform.
    cpu_vendor: str

    #: The short name (``tdx`` / ``snp``): what the CLI prints, and the platform's segment in the
    #: Chutes API routes (``/servers/<name>/...``).
    name: str

    @classmethod
    def for_cpu_vendor(cls, cpu_vendor: str) -> "type[TeeProvider]":
        """The platform a host's silicon runs, from its CPUID vendor string.

        The identity question, and the one a host profile answers: this silicon runs that TEE.
        Distinct from ``enabled_on_host``, the capability question (is it switched on right
        now), which is a BIOS setting rather than a property of the silicon.
        """
        vendor = (cpu_vendor or "").strip()
        for provider_type in TeeProvider.__subclasses__():
            if provider_type.cpu_vendor == vendor:
                return provider_type
        raise ValueError(
            f"cannot determine the TEE for CPU vendor {cpu_vendor!r}; expected "
            "GenuineIntel (TDX) or AuthenticAMD (SEV-SNP)"
        )

    @classmethod
    def enabled_on_host(cls) -> "type[TeeProvider]":
        """The platform this host has switched on, from the kvm module parameters.

        Not the CPU vendor: a Genoa host with SEV-SNP disabled in BIOS reports AMD but cannot
        launch an SNP guest, and failing here is far clearer than failing inside QEMU.
        """
        for provider_type in TeeProvider.__subclasses__():
            if _module_param_enabled(provider_type.kvm_param):
                return provider_type
        raise RuntimeError(
            "No confidential-computing platform is enabled on this host: neither "
            f"{KVM_INTEL_TDX} nor {KVM_AMD_SEV_SNP} reads as enabled. Whether a platform is "
            "on is a BIOS setting, not a property of the silicon; the per-platform remedy "
            "is on the provider (TeeProvider.verify_environment)."
        )

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

    #: Whether a guest on this platform can be given the 2-node guest-NUMA topology. A
    #: platform fact rather than a host one: the host can offer the nodes and the guest
    #: still fail to boot on them. ``HostProfile.uses_guest_numa`` combines the two.
    supports_guest_numa: bool

    #: Extra ``-machine`` options this platform's guests launch with.
    machine_opts: "tuple[str, ...]" = ()

    #: What this platform switches off in the guest whether or not ``-machine`` says so. A fact
    #: about the platform at runtime; a QEMU with no TEE (the offline dump) needs them spelled out
    #: to produce the guest's ACPI tables byte for byte.
    implicit_machine_opts: "tuple[str, ...]"

    #: kvm module parameter that reports this platform enabled on the running host.
    kvm_param: str

    #: What to do about it when that parameter reads disabled. Per-platform because the
    #: remedy is: the BIOS settings differ, and a message naming the wrong vendor's
    #: switches is worse than none.
    enablement_hint: str

    @abstractmethod
    def guest_object(self) -> str:
        """The ``-object`` argument declaring the confidential guest."""

    def devices(self) -> "tuple[str, ...]":
        """Emulated devices this platform's guests need, without a slot (the caller places them).

        None by default: anything attached is a host-controlled channel into the guest.
        """
        return ()

    def verify_environment(self) -> None:
        """Raise unless THIS provider's platform is switched on for this host.

        The capability question, and deliberately not the identity one. The profile says
        which platform a host class runs (CPU vendor, see ``for_cpu_vendor``);
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
            detail = f"{TeeProvider.enabled_on_host().name} enabled instead"
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
        """The launch ``-machine`` argument, optionally bound to a flat memory backend."""
        parts = [
            Q35_MACHINE,
            f"confidential-guest-support={self.guest_id}",
            *self.machine_opts,
        ]
        if memory_backend:
            parts.append(f"memory-backend={memory_backend}")
        return ",".join(parts)


class TdxTeeProvider(TeeProvider):
    """Intel TDX."""

    cpu_vendor = "GenuineIntel"
    name = "tdx"
    guest_id = "tdx"
    smbios_product = "TDX-VM"
    default_firmware = "OVMF.inteltdx.fd"
    label = "Intel TDX"
    memory_backend_type = "memory-backend-ram"
    supports_guest_numa = True
    # A TD runs with SMM and the legacy PIC off whether or not -machine says so.
    implicit_machine_opts = ("smm=off", "pic=off")
    kvm_param = KVM_INTEL_TDX
    enablement_hint = (
        "Either the profile was captured on other hardware, or Intel TDX is not "
        "enabled in BIOS on this host."
    )

    def guest_object(self) -> str:
        """The ``-object`` argument declaring the confidential guest."""
        # The quote-generation socket reaches qgsd on the host over vsock; TDX
        # quotes are produced outside the guest, unlike SNP reports.
        return (
            '{"qom-type":"tdx-guest","id":"tdx",'
            '"quote-generation-socket":{"type":"vsock","cid":"2","port":"4050"}}'
        )

    def devices(self) -> "tuple[str, ...]":
        # The guest end of the vsock the quote-generation socket above dials.
        return ("vhost-vsock-pci,guest-cid=3",)


# Where SEV puts the C-bit in a guest physical address, and the address bits that costs (CPUID
# Fn8000_001F EBX[5:0] and [11:6]). Every SNP-capable EPYC so far puts the C-bit at 51, and QEMU
# refuses a launch whose cbitpos differs from the host's. reduced-phys-bits only needs to be at
# least 1: neither value is measured, and live launches with 1 and the hardware's 5 measure alike.
SNP_CBITPOS = 51
SNP_REDUCED_PHYS_BITS = 1

# The SEV-SNP guest policy bits the launcher sets. NOT a launch-digest input: the policy
# travels in the attestation report, and the API requires exactly this value there -- a
# measurement match says nothing about the policy. Change both together.
#   bit 16 SMT allowed, bit 17 reserved (must be 1); DEBUG (19) and all else clear.
SNP_DEFAULT_POLICY = 0x30000


class SnpTeeProvider(TeeProvider):
    """AMD SEV-SNP."""

    cpu_vendor = "AuthenticAMD"
    name = "snp"
    guest_id = "snp0"
    smbios_product = "SNP-VM"
    default_firmware = "OVMF.amdsev.fd"
    label = "AMD SEV-SNP"
    memory_backend_type = "memory-backend-memfd"
    memory_backend_opts = ("share=on",)
    # Flat until QEMU can convert a range that spans two guest_memfd backends. SNP accepts
    # every page through a hypervisor Page State Change, and the kernel extends each accept
    # one 2 MB unit past a unit-aligned end, so accepting the last unit of node 0 also
    # converts the first unit of node 1. KVM hands QEMU that 4 MB as one range and
    # kvm_convert_memory() rejects it ("ram_block_attributes_state_change, invalid range"),
    # wedging the guest during NUMA init. TDX accepts pages inside the TDX module without
    # an exit, so it never reaches that path. Fixed upstream by "accel/kvm: Fix
    # kvm_convert_memory() calls crossing memory regions" (not in QEMU 10.2.1); flip this
    # back once the pinned QEMU carries it.
    supports_guest_numa = False
    # NVIDIA's confidential-computing deployment guide specifies vmport=off for SEV-SNP guests.
    machine_opts = ("vmport=off",)
    # QEMU switches SMM off for an SEV-ES/SNP guest; the PIC stays. Verified against a live guest:
    # with SMM left on the dump's FADT carries an SMI_CMD port the guest's does not.
    implicit_machine_opts = ("smm=off",)
    kvm_param = KVM_AMD_SEV_SNP
    enablement_hint = (
        "Either the profile was captured on other hardware, or SEV-SNP is not enabled "
        "in BIOS — check that SEV-SNP Support and SMEE are on, SEV-ES ASID Space Limit "
        "is non-trivial (1 leaves zero usable SNP ASIDs), and TSME is off."
    )

    def __init__(self, policy: int = SNP_DEFAULT_POLICY, kernel_hashes: bool = True):
        # Bits 16 (SMT allowed) and 17 (reserved, must be 1). Bit 19 (DEBUG) is
        # deliberately clear: with it set the host can decrypt and inspect guest
        # memory, and the report would still carry a valid signature.
        self.policy = policy
        # Puts SHA-256 of kernel/initrd/cmdline in a measured page, which OVMF
        # then enforces before boot. This is what makes the measured-initrd key
        # release gate work on SNP, so it is not optional.
        self.kernel_hashes = kernel_hashes

    def guest_object(self) -> str:
        """The ``-object`` argument declaring the confidential guest."""
        obj = (
            f"sev-snp-guest,id={self.guest_id},"
            f"cbitpos={SNP_CBITPOS},"
            f"reduced-phys-bits={SNP_REDUCED_PHYS_BITS},"
            f"policy={self.policy:#x}"
        )
        if self.kernel_hashes:
            obj += ",kernel-hashes=on"
        return obj


def tee_firmware_available(provider: TeeProvider, firmware_dir: str) -> bool:
    """True when this platform's firmware image is present in ``firmware_dir``."""
    return os.path.exists(os.path.join(firmware_dir, provider.default_firmware))
