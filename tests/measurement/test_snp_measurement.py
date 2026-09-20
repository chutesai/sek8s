"""Offline SEV-SNP launch-measurement generation."""

import hashlib
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from chutes_cvm.guest.tee import SNP_DEFAULT_POLICY, tee_for_cpu_vendor
from chutes_cvm.measurement.runtime_rtmr import MeasurementError
from chutes_cvm.measurement.snp_measurement import (
    compute_snp_measurement,
    cpu_fms_from_processor_id,
    direct_boot_artifacts,
)

REPO = Path(__file__).resolve().parents[2]

# EPYC 9124 (Genoa) as captured from a real host, and the digest it produces for the
# 1.4.0-debug image — both taken from live SEV-SNP hardware.
GENOA_PROCESSOR_ID = "110fa100fffba91f"
GENOA_FMS = (25, 17, 1)

# EPYC 7763 (Milan) — the 8x RTX PRO 6000 SEV-SNP host, from its /proc/cpuinfo.
MILAN_FMS = (25, 1, 1)
MILAN_PROCESSOR_ID = "110fa000fffba91f"
# The Intel host the shipped RTX_PRO_6000 profile was captured on.
INTEL_PROCESSOR_ID = "f3060a00fffba91f"


def _shipped_encoder(
    family: int, model: int, stepping: int, edx: str = "0x1FA9FBFF"
) -> str:
    """Run the processor_id encoder exactly as shipped.

    It lives as an embedded Python heredoc inside discover-profile.sh -- the capture side is
    bash -- so the round-trip extracts and runs *that* source rather than restating it here.
    A restatement would agree with a broken decoder; this disagrees the moment either side
    moves.
    """
    script = (
        REPO / "src/chutes-cvm/chutes_cvm/scripts/discover-profile.sh"
    ).read_text()
    body = re.search(r"<<'PY'[^\n]*\n(.*?)\nPY\n", script, re.S)
    assert (
        body
    ), "could not find the processor_id encoder heredoc in discover-profile.sh"
    out = subprocess.run(
        ["python3", "-", str(family), str(model), str(stepping), edx],
        input=body.group(1),
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


# ── CPU identity ──────────────────────────────────────────────────────────────


def test_decodes_a_real_amd_processor_id():
    """Captured from the EPYC 9124 SEV-SNP host, cross-checked against its /proc/cpuinfo."""
    assert cpu_fms_from_processor_id(GENOA_PROCESSOR_ID) == GENOA_FMS


@pytest.mark.parametrize(
    "family,model,stepping",
    [
        (25, 17, 1),  # EPYC Genoa (the SEV-SNP dev host)
        (25, 1, 1),  # EPYC Milan
        (26, 2, 0),  # EPYC Turin (family >= 0xF, extended family)
        (6, 143, 8),  # Sapphire Rapids (family < 0xF, extended model)
        (6, 15, 2),  # base model only, no extended bits
    ],
)
def test_decode_is_the_exact_inverse_of_the_shipped_encoder(family, model, stepping):
    """Round-trips against the encoder that actually produces host profiles, so a change
    to its packing fails here instead of silently disagreeing with the decoder."""
    processor_id = _shipped_encoder(family, model, stepping)

    assert cpu_fms_from_processor_id(processor_id) == (family, model, stepping)


@pytest.mark.parametrize(
    "fms,processor_id",
    [
        (GENOA_FMS, GENOA_PROCESSOR_ID),  # EPYC 9124, the H100 PCIe SEV-SNP host
        (MILAN_FMS, MILAN_PROCESSOR_ID),  # EPYC 7763, the 8x RTX PRO 6000 SEV-SNP host
    ],
)
def test_shipped_encoder_reproduces_the_real_hosts(fms, processor_id):
    """Anchors the round-trip to hardware: fed each host's real /proc/cpuinfo values, the
    encoder must produce the processor_id that host's capture carries. Two different Zen
    generations, so a family/model packing error cannot pass by coincidence."""
    assert _shipped_encoder(*fms) == processor_id
    assert cpu_fms_from_processor_id(processor_id) == fms


def test_missing_processor_id_is_refused():
    """Falling back to the generating host's CPU would emit a plausible measurement for
    the wrong machine — the same stance measurement_cpu_args takes for RTMR0."""
    with pytest.raises(MeasurementError, match="no cpu_processor_id"):
        cpu_fms_from_processor_id(None)
    with pytest.raises(MeasurementError, match="no cpu_processor_id"):
        cpu_fms_from_processor_id("")


def test_malformed_processor_id_is_refused():
    with pytest.raises(MeasurementError, match="malformed"):
        cpu_fms_from_processor_id("nothex!!")
    with pytest.raises(MeasurementError, match="too short"):
        cpu_fms_from_processor_id("1122")


# ── vendor → TEE ──────────────────────────────────────────────────────────────


def test_tee_follows_the_cpu_vendor():
    assert tee_for_cpu_vendor("AuthenticAMD") == "snp"
    assert tee_for_cpu_vendor("GenuineIntel") == "tdx"


def test_unknown_vendor_is_refused():
    with pytest.raises(ValueError, match="cannot determine the TEE"):
        tee_for_cpu_vendor("SomeOtherVendor")
    with pytest.raises(ValueError, match="cannot determine the TEE"):
        tee_for_cpu_vendor("")


# ── direct-boot artifacts ─────────────────────────────────────────────────────


def _stage_image(tmp_path, cmdline="root=UUID=abc ro console=ttyS0"):
    image = tmp_path / "guest.qcow2"
    image.write_bytes(b"")
    (tmp_path / "guest.vmlinuz").write_bytes(b"kernel")
    (tmp_path / "guest.initrd").write_bytes(b"initrd")
    (tmp_path / "guest.cmdline").write_text(cmdline + "\n")
    return image


def test_artifacts_resolve_beside_the_image(tmp_path):
    image = _stage_image(tmp_path)
    kernel, initrd, cmdline = direct_boot_artifacts(str(image))

    assert Path(kernel).name == "guest.vmlinuz"
    assert Path(initrd).name == "guest.initrd"
    # Trailing newline stripped, matching $(cat file) semantics in the launcher.
    assert cmdline == "root=UUID=abc ro console=ttyS0"


def test_missing_artifact_is_reported(tmp_path):
    image = _stage_image(tmp_path)
    (tmp_path / "guest.initrd").unlink()

    with pytest.raises(MeasurementError, match="missing direct-boot artifact"):
        direct_boot_artifacts(str(image))


# ── the generator ─────────────────────────────────────────────────────────────


def _fake_run(stdout="", returncode=0, stderr="", capture=None):
    def run(cmd, **kwargs):
        if capture is not None:
            capture.extend(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    return run


def test_measurement_is_generated_from_the_pinned_inputs(tmp_path):
    image = _stage_image(tmp_path)
    firmware = tmp_path / "OVMF.amdsev.fd"
    firmware.write_bytes(b"firmware")
    digest = "ab" * 48
    cmd = []

    with patch(
        "chutes_cvm.measurement.snp_measurement.proc.run",
        _fake_run(stdout=digest + "\n", capture=cmd),
    ):
        result = compute_snp_measurement(
            str(image), str(firmware), 28, GENOA_PROCESSOR_ID
        )

    assert result == digest.upper()
    # The CPU identity must reach the tool as family/model/stepping, not a vcpu-type
    # name: the launcher uses `-cpu host`, so a named model is a different VMSA.
    assert "--vcpu-family" in cmd and cmd[cmd.index("--vcpu-family") + 1] == "25"
    assert cmd[cmd.index("--vcpu-model") + 1] == "17"
    assert cmd[cmd.index("--vcpu-stepping") + 1] == "1"
    assert cmd[cmd.index("--vcpus") + 1] == "28"
    assert cmd[cmd.index("--ovmf") + 1] == str(firmware)
    assert "--vcpu-type" not in cmd


def test_missing_firmware_is_refused(tmp_path):
    image = _stage_image(tmp_path)

    with pytest.raises(MeasurementError, match="guest firmware"):
        compute_snp_measurement(
            str(image), str(tmp_path / "absent.fd"), 28, GENOA_PROCESSOR_ID
        )


def test_tool_failure_is_surfaced(tmp_path):
    image = _stage_image(tmp_path)
    firmware = tmp_path / "OVMF.amdsev.fd"
    firmware.write_bytes(b"firmware")

    with patch(
        "chutes_cvm.measurement.snp_measurement.proc.run",
        _fake_run(returncode=2, stderr="bad vcpu count"),
    ):
        with pytest.raises(MeasurementError, match="bad vcpu count"):
            compute_snp_measurement(str(image), str(firmware), 28, GENOA_PROCESSOR_ID)


def test_unexpected_tool_output_is_refused(tmp_path):
    """A short or non-hex line must not be written into measurements.yaml as if it were
    a measurement."""
    image = _stage_image(tmp_path)
    firmware = tmp_path / "OVMF.amdsev.fd"
    firmware.write_bytes(b"firmware")

    with patch(
        "chutes_cvm.measurement.snp_measurement.proc.run",
        _fake_run(stdout="not-a-measurement\n"),
    ):
        with pytest.raises(MeasurementError, match="unexpected measurement"):
            compute_snp_measurement(str(image), str(firmware), 28, GENOA_PROCESSOR_ID)


def test_missing_binary_is_reported(tmp_path):
    image = _stage_image(tmp_path)
    firmware = tmp_path / "OVMF.amdsev.fd"
    firmware.write_bytes(b"firmware")

    def raise_missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    with patch("chutes_cvm.measurement.snp_measurement.proc.run", raise_missing):
        with pytest.raises(MeasurementError, match="not found on PATH"):
            compute_snp_measurement(str(image), str(firmware), 28, GENOA_PROCESSOR_ID)


# ── pinned firmware ───────────────────────────────────────────────────────────


def test_amd_firmware_is_pinned_in_the_repo():
    """The launch digest is a hash of these exact bytes, so the firmware must ship with
    the repo rather than being picked up from /usr/share/ovmf at measurement time."""
    firmware = REPO / "firmware" / "OVMF.amdsev.fd"
    assert firmware.is_file(), "firmware/OVMF.amdsev.fd is missing"

    recorded = re.search(
        r"sha256\s+([0-9a-f]{64})", (REPO / "firmware" / "PROVENANCE.md").read_text()
    )
    assert recorded, "PROVENANCE.md records no sha256 for OVMF.amdsev.fd"
    actual = hashlib.sha256(firmware.read_bytes()).hexdigest()
    assert actual == recorded.group(1), (
        "firmware/OVMF.amdsev.fd does not match the digest in PROVENANCE.md — every "
        "published SEV-SNP measurement was computed against the recorded bytes"
    )


def test_guest_policy_is_shared_with_the_launcher():
    """Not a measurement input, but it is reported and checked, so launch and docs must
    agree on one value rather than restating 0x30000."""
    assert SNP_DEFAULT_POLICY == 0x30000
    assert not SNP_DEFAULT_POLICY & (1 << 19), "DEBUG must be clear"


# ── per-TEE dispatch in the single generator ──────────────────────────────────


def _host_doc(vendor: str, processor_id: "str | None"):
    """A real discover-profile document for an 8x RTX PRO 6000 host of ``vendor``.

    The same GPU genuinely appears on both platforms -- the shipped RTX_PRO_6000 profile was
    captured on an Intel Xeon host, while the SEV-SNP box carrying those GPUs is an EPYC 7763
    -- so one GPU profile across two TEEs is the real case, not a contrived one.
    """
    import topology_fixtures as known

    doc = known.host_document(
        "RTX_PRO_6000",
        vcpus=124,
        gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1),
        cpu_vendor=vendor,
        cpu_processor_id=processor_id,
    )
    return doc


def _records(*specs):
    """(api records, {fingerprint: HostProfile}) for the given (name, vendor, pid) specs."""
    from chutes_cvm.guest.host_profile import HostProfile

    records, hosts = [], {}
    for name, vendor, pid in specs:
        fp = f"{name}-fp"
        records.append({"fingerprint": fp, "profile": {"name": name}})
        hosts[name] = HostProfile(_host_doc(vendor, pid))
    return records, hosts


def _args(tmp_path):
    import argparse

    return argparse.Namespace(
        version="1.4.0",
        image=str(tmp_path / "guest.qcow2"),
        bios_dir=str(REPO / "firmware"),
        api_base="https://api.test",
        include_pending=False,
        tdx_measure_bin="tdx-measure",
        sev_snp_measure_bin="sev-snp-measure",
        dist="noble",
    )


def _patched(gm, hosts, **extra):
    """Patch the API read and the profile parse; callers add the generators."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch.object(gm, "fetch_host_profiles", return_value=extra.pop("records"))
    )
    stack.enter_context(
        patch.object(
            gm.HostProfile,
            "from_api_profile",
            side_effect=lambda doc: hosts[doc["name"]],
        )
    )
    return stack


def test_each_host_class_gets_the_measurement_its_cpu_needs(tmp_path):
    """One pass over the published host profiles produces Intel RTMR0 entries and AMD
    launch-digest entries side by side — the CPU vendor on the profile decides which."""
    import chutes_cvm.measurement.generate_measurements as gm

    _stage_image(tmp_path)
    records, hosts = _records(
        ("intel-rtx", "GenuineIntel", INTEL_PROCESSOR_ID),
        ("amd-rtx", "AuthenticAMD", MILAN_PROCESSOR_ID),
    )

    with _patched(gm, hosts, records=records) as stack:
        stack.enter_context(
            patch.object(
                gm,
                "generate_acpi_blobs",
                return_value={"rtmr0": "r0hex", "mrtd": "mrtdhex"},
            )
        )
        snp = stack.enter_context(
            patch.object(gm, "compute_snp_measurement", return_value="M" * 96)
        )
        block = gm._hardware_blocks(_args(tmp_path))

    # One entry each, and the label is the profile's own (it is derived, not asserted here).
    assert len(block["tdx_hardware"]) == 1 and len(block["snp_hardware"]) == 1
    assert block["tdx_hardware"][0]["name"].startswith("8xpro_6000 [10.2.1,")
    assert block["snp_hardware"][0]["name"].startswith("8xpro_6000 [10.2.1,")
    assert block["tdx_hardware"][0]["rtmr0"] == "R0HEX"
    assert block["snp_hardware"][0]["measurement"] == "M" * 96
    # SNP is measured against the PINNED AMD firmware, never the TDX one.
    assert snp.call_args.args[1].endswith("OVMF.amdsev.fd")
    # The vCPU count and CPU identity reach the generator from the host profile.
    assert snp.call_args.args[2] == 124
    assert snp.call_args.args[3] == MILAN_PROCESSOR_ID


def test_colliding_names_across_tees_are_disambiguated(tmp_path):
    """Both lands in one measurements.yaml, and the two classes above share a display name
    because only their CPU differs — so the suffixing has to span both lists."""
    import chutes_cvm.measurement.generate_measurements as gm

    _stage_image(tmp_path)
    records, hosts = _records(
        ("intel-rtx", "GenuineIntel", INTEL_PROCESSOR_ID),
        ("amd-rtx", "AuthenticAMD", MILAN_PROCESSOR_ID),
    )

    with _patched(gm, hosts, records=records) as stack:
        stack.enter_context(
            patch.object(
                gm,
                "generate_acpi_blobs",
                return_value={"rtmr0": "r0hex", "mrtd": "mrtdhex"},
            )
        )
        stack.enter_context(
            patch.object(gm, "compute_snp_measurement", return_value="M" * 96)
        )
        block = gm._hardware_blocks(_args(tmp_path))

    names = [e["name"] for e in block["tdx_hardware"] + block["snp_hardware"]]
    assert len(set(names)) == 2, f"names not disambiguated across TEEs: {names}"
    assert all(n.endswith(")") for n in names)  # suffixed with the fingerprint


def test_rtmr0_partial_skips_amd_classes(tmp_path):
    """`--register rtmr0` is a TDX-only debug partial and has no image to measure SNP
    from, so AMD classes are simply absent rather than pending."""
    import chutes_cvm.measurement.generate_measurements as gm

    records, hosts = _records(("amd-rtx", "AuthenticAMD", MILAN_PROCESSOR_ID))

    with _patched(gm, hosts, records=records) as stack:
        snp = stack.enter_context(patch.object(gm, "compute_snp_measurement"))
        block = gm._hardware_blocks(_args(tmp_path), include_snp=False)

    assert block["snp_hardware"] == []
    assert block["tdx_hardware"] == []
    snp.assert_not_called()


def test_a_class_that_cannot_be_generated_is_pending_not_fatal(tmp_path):
    """An AMD class whose CPU model was never captured must not take the whole release
    down — it is listed pending, exactly as an ungeneratable Intel class is."""
    import chutes_cvm.measurement.generate_measurements as gm

    _stage_image(tmp_path)
    records, hosts = _records(
        ("amd-ok", "AuthenticAMD", MILAN_PROCESSOR_ID),
        ("amd-nocpu", "AuthenticAMD", None),
    )

    with _patched(gm, hosts, records=records) as stack:
        stack.enter_context(
            patch.object(
                gm,
                "compute_snp_measurement",
                side_effect=lambda image, fw, vcpus, pid, **kw: (
                    "M" * 96
                    if pid
                    else (_ for _ in ()).throw(MeasurementError("no cpu_processor_id"))
                ),
            )
        )
        block = gm._hardware_blocks(_args(tmp_path))

    assert len(block["snp_hardware"]) == 1
    assert block["pending_profiles"] == ["amd-nocpu-fp"]
