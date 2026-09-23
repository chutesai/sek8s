import pytest
from chutes_cvm.guest import tee as tee_module
from chutes_cvm.guest.qemu import PcieRootPinning, build_base_cmd
from chutes_cvm.guest.tee import (
    DEFAULT_CBITPOS,
    DEFAULT_REDUCED_PHYS_BITS,
    HostTee,
    SnpTeeProvider,
    TdxTeeProvider,
    detect_host_tee,
    sev_cbit_parameters,
)


def _params(**enabled):
    """Fake the kvm module parameter files."""

    def _reader(path):
        return enabled.get(path, False)

    return _reader


def test_detect_host_tee_finds_tdx(monkeypatch):
    monkeypatch.setattr(
        tee_module, "_module_param_enabled", _params(**{tee_module.KVM_INTEL_TDX: True})
    )
    assert detect_host_tee() is HostTee.TDX


def test_detect_host_tee_finds_snp(monkeypatch):
    monkeypatch.setattr(
        tee_module,
        "_module_param_enabled",
        _params(**{tee_module.KVM_AMD_SEV_SNP: True}),
    )
    assert detect_host_tee() is HostTee.SNP


def test_detect_host_tee_raises_when_neither_enabled(monkeypatch):
    # An AMD host with SEV-SNP disabled in BIOS still reports AMD, so detection
    # keys off the kvm parameter rather than the CPU vendor.
    monkeypatch.setattr(tee_module, "_module_param_enabled", _params())
    with pytest.raises(RuntimeError, match="No confidential-computing platform"):
        detect_host_tee()


def test_detect_host_tee_override(monkeypatch):
    monkeypatch.setattr(tee_module, "_module_param_enabled", _params())
    assert detect_host_tee("snp") is HostTee.SNP


def test_verify_environment_passes_when_its_own_platform_is_on(monkeypatch):
    monkeypatch.setattr(
        tee_module, "_module_param_enabled", _params(**{tee_module.KVM_INTEL_TDX: True})
    )
    TdxTeeProvider().verify_environment()  # does not raise


def test_verify_environment_names_the_platform_that_is_actually_on(monkeypatch):
    """An AMD class on a box reporting TDX: either the profile came from other hardware
    or SEV-SNP is off in BIOS. Both are named, because the operator cannot tell which.
    """
    monkeypatch.setattr(
        tee_module, "_module_param_enabled", _params(**{tee_module.KVM_INTEL_TDX: True})
    )
    with pytest.raises(RuntimeError) as exc:
        SnpTeeProvider().verify_environment()

    msg = str(exc.value)
    assert "AMD SEV-SNP" in msg and "tdx enabled instead" in msg
    assert tee_module.KVM_AMD_SEV_SNP in msg


def test_verify_environment_when_no_platform_is_enabled(monkeypatch):
    monkeypatch.setattr(tee_module, "_module_param_enabled", _params())
    with pytest.raises(RuntimeError, match="no confidential-computing platform"):
        SnpTeeProvider().verify_environment()


def test_each_provider_carries_its_own_parameter_and_remedy():
    """The remedy is platform-specific, so it lives on the provider. A single shared
    message listed the AMD BIOS switches even when the failure was on an Intel host."""
    assert TdxTeeProvider.kvm_param == tee_module.KVM_INTEL_TDX
    assert SnpTeeProvider.kvm_param == tee_module.KVM_AMD_SEV_SNP
    assert "SMEE" in SnpTeeProvider.enablement_hint
    assert "SMEE" not in TdxTeeProvider.enablement_hint


def test_module_param_enabled_reads_y(tmp_path):
    param = tmp_path / "sev_snp"
    param.write_text("Y\n")
    assert tee_module._module_param_enabled(str(param)) is True
    param.write_text("N\n")
    assert tee_module._module_param_enabled(str(param)) is False


def test_module_param_enabled_missing_file_is_false():
    assert tee_module._module_param_enabled("/nonexistent/param") is False


def test_tdx_provider_emits_quote_socket():
    tdx = TdxTeeProvider()
    obj = tdx.guest_object()
    assert '"qom-type":"tdx-guest"' in obj
    # TDX quotes come from qgsd on the host over vsock; SNP has no equivalent.
    assert "quote-generation-socket" in obj


def test_tdx_machine_and_backend_are_unchanged():
    tdx = TdxTeeProvider()
    assert tdx.machine("mem0") == (
        "q35,kernel_irqchip=split,confidential-guest-support=tdx,memory-backend=mem0"
    )
    assert tdx.machine(None) == (
        "q35,kernel_irqchip=split,confidential-guest-support=tdx"
    )
    assert tdx.memory_backend("mem0", "8G") == "memory-backend-ram,id=mem0,size=8G"
    assert tdx.memory_backend("mem-node1", "4096M", host_node=1) == (
        "memory-backend-ram,id=mem-node1,size=4096M,host-nodes=1,policy=bind"
    )


def test_snp_guest_object_carries_cbit_policy_and_kernel_hashes():
    snp = SnpTeeProvider(cbitpos=51, reduced_phys_bits=1)
    obj = snp.guest_object()
    assert obj.startswith("sev-snp-guest,id=snp0")
    assert "cbitpos=51" in obj
    assert "reduced-phys-bits=1" in obj
    # kernel-hashes is what puts the initrd in the launch measurement, which is
    # what the measured-initrd key-release gate depends on.
    assert "kernel-hashes=on" in obj


def test_snp_policy_leaves_debug_bit_clear():
    snp = SnpTeeProvider()
    # Bit 19 set would let the host decrypt guest memory while the report still
    # carried a valid signature.
    assert not snp.policy & (1 << 19)
    assert snp.policy & (1 << 16)  # SMT allowed
    assert snp.policy & (1 << 17)  # reserved, must be 1


def test_snp_machine_disables_vmport():
    snp = SnpTeeProvider()
    machine = snp.machine("mem0")
    assert "confidential-guest-support=snp0" in machine
    assert "vmport=off" in machine
    assert "memory-backend=mem0" in machine


def test_snp_memory_backend_is_shared_memfd():
    snp = SnpTeeProvider()
    # SNP private memory is served from guest_memfd; memory-backend-ram fails.
    assert snp.memory_backend("mem0", "8G") == (
        "memory-backend-memfd,id=mem0,size=8G,share=on"
    )
    assert snp.memory_backend("mem-node0", "4096M", host_node=0) == (
        "memory-backend-memfd,id=mem-node0,size=4096M,share=on,host-nodes=0,policy=bind"
    )


def test_sev_cbit_parameters_falls_back_when_cpuid_unavailable(monkeypatch):
    monkeypatch.setattr(tee_module, "CPUID_DEVICE", "/nonexistent/cpuid")
    assert sev_cbit_parameters() == (DEFAULT_CBITPOS, DEFAULT_REDUCED_PHYS_BITS)


def test_sev_cbit_parameters_reads_cpuid(monkeypatch, tmp_path):
    import struct

    cpuid = tmp_path / "cpuid"
    # EBX[5:0] = 51 (C-bit), EBX[11:6] = 1 (phys addr bits lost)
    ebx = 51 | (1 << 6)
    # /dev/cpu/N/cpuid is seek-addressed: leaf N lives at offset N*16. Seek and write
    # the one 16-byte entry, leaving a SPARSE file — materialising the offset would be
    # a 34 GB allocation (SEV_CPUID_LEAF is 0x8000001F).
    with open(cpuid, "wb") as f:
        f.seek(tee_module.SEV_CPUID_LEAF * 16)
        f.write(struct.pack("<IIII", 0, ebx, 0, 0))
    monkeypatch.setattr(tee_module, "CPUID_DEVICE", str(cpuid))

    assert sev_cbit_parameters() == (51, 1)


def _base_cmd(tee, tmp_path, host_nodes=()):
    import topology_fixtures as known

    return build_base_cmd(
        known.QemuProfileStub(
            mem="8G",
            smp_topology="cpus=4,sockets=1,cores=2,threads=2",
            uses_guest_numa=len(host_nodes) >= 2,
            tee_provider=tee,
        ),
        process_name="chutes-td",
        firmware=str(tmp_path / "OVMF.fd"),
        img_path=str(tmp_path / "root.qcow2"),
        foreground=False,
        pidfile="/dev/null",
        logfile="/dev/null",
        host_nodes=list(host_nodes),
        kernel_path="/dev/null",
        initrd_path="/dev/null",
        cmdline="",
        pci_pinning=PcieRootPinning(len(host_nodes) >= 2),
    )


def test_build_base_cmd_with_tdx_provider(tmp_path):
    args = " ".join(_base_cmd(TdxTeeProvider(), tmp_path).to_args())
    assert "tdx-guest" in args
    assert "memory-backend-ram,id=mem0" in args
    assert "product=TDX-VM" in args


def test_build_base_cmd_refuses_host_nodes_that_contradict_the_profile(tmp_path):
    """One source of truth for guest shape. A host whose live NUMA disagrees with its
    captured profile would otherwise get PXB bridges pinned from the profile and flat
    memory args derived from sysfs -- a command no measurement was generated for."""
    with pytest.raises(ValueError, match="does not match the profile"):
        build_base_cmd(
            _tdx_stub(uses_guest_numa=True),
            process_name="chutes-td",
            firmware=str(tmp_path / "OVMF.fd"),
            img_path=str(tmp_path / "root.qcow2"),
            foreground=False,
            pidfile="/dev/null",
            logfile="/dev/null",
            host_nodes=[],  # profile says NUMA, the machine reports one node
            kernel_path="/dev/null",
            initrd_path="/dev/null",
            cmdline="",
            pci_pinning=PcieRootPinning(True),
        )


def _tdx_stub(**over):
    import topology_fixtures as known

    return known.QemuProfileStub(
        mem="8G", smp_topology="cpus=4,sockets=1,cores=2,threads=2", **over
    )


def test_build_base_cmd_with_snp_provider(tmp_path):
    args = " ".join(_base_cmd(SnpTeeProvider(cbitpos=51), tmp_path).to_args())
    assert "sev-snp-guest,id=snp0" in args
    assert "memory-backend-memfd,id=mem0,size=8G,share=on" in args
    assert "confidential-guest-support=snp0" in args
    assert "vmport=off" in args
    assert "product=SNP-VM" in args
    assert "tdx-guest" not in args


def test_build_base_cmd_snp_numa_backends_are_memfd(tmp_path):
    cmd = _base_cmd(SnpTeeProvider(), tmp_path, host_nodes=[0, 1])
    backends = [o for o in cmd.objects if o.startswith("memory-backend")]
    assert len(backends) == 2
    assert all("memory-backend-memfd" in b and "share=on" in b for b in backends)
    assert all("policy=bind" in b for b in backends)


# ── the platform is a property of the host class, not a detection ─────────────


def _profile(vendor):
    import topology_fixtures as known
    from chutes_cvm.guest.host_profile import HostProfile

    return HostProfile(
        known.host_document(
            "RTX_PRO_6000", vcpus=124, gpu_nodes=(0,) * 8, cpu_vendor=vendor
        )
    )


def test_host_profile_derives_its_own_platform():
    """A class is Intel or AMD silicon, so the profile already determines the TEE --
    nothing needs to be detected or passed alongside it. The provider IS the identity;
    there is no platform string beside it to disagree with."""
    assert isinstance(_profile("GenuineIntel").tee_provider, TdxTeeProvider)
    assert isinstance(_profile("AuthenticAMD").tee_provider, SnpTeeProvider)


def test_derived_platform_selects_its_own_firmware():
    """Firmware is a property of the platform, so it follows from the profile too."""
    assert _profile("GenuineIntel").tee_provider.default_firmware == "OVMF.inteltdx.fd"
    assert _profile("AuthenticAMD").tee_provider.default_firmware == "OVMF.amdsev.fd"


def test_qemu_command_uses_the_derived_platform(tmp_path):
    """The command a host launches with carries its own platform's guest object."""
    intel = _profile("GenuineIntel").qemu_command(firmware=str(tmp_path / "f.fd"))
    amd = _profile("AuthenticAMD").qemu_command(firmware=str(tmp_path / "f.fd"))

    assert "tdx-guest" in intel.tee_object
    assert "sev-snp-guest" in amd.tee_object
    assert "vmport=off" in amd.machine and "vmport=off" not in intel.machine


def test_unknown_vendor_has_no_platform():
    """Fail on the profile rather than silently defaulting to one platform."""
    with pytest.raises(ValueError, match="cannot determine the TEE"):
        _profile("SomeOtherVendor").tee_provider
