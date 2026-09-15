"""Offline SEV-SNP launch-measurement generation."""

import hashlib
import re
import subprocess
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from chutes_cvm.guest.detection import detect_host_cpu_identity
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


def _cpuinfo(vendor, family, model, stepping):
    return (
        f"vendor_id\t: {vendor}\n"
        f"cpu family\t: {family}\n"
        f"model\t\t: {model}\n"
        f"stepping\t: {stepping}\n"
    )


# ── CPU identity ──────────────────────────────────────────────────────────────


def test_decodes_a_real_amd_processor_id():
    assert cpu_fms_from_processor_id(GENOA_PROCESSOR_ID) == GENOA_FMS


@pytest.mark.parametrize(
    "vendor,family,model,stepping",
    [
        ("AuthenticAMD", 25, 17, 1),  # EPYC Genoa
        ("AuthenticAMD", 25, 1, 1),  # EPYC Milan
        ("AuthenticAMD", 26, 2, 0),  # EPYC Turin (family >= 0xF, extended)
        ("GenuineIntel", 6, 143, 8),  # Sapphire Rapids (family < 0xF, extended model)
        ("GenuineIntel", 6, 15, 2),  # base model only, no extended bits
    ],
)
def test_decode_is_the_exact_inverse_of_detection(vendor, family, model, stepping):
    """Round-trips against the shipped encoder rather than a restatement of it: if
    detection's packing ever changes, this fails instead of silently disagreeing."""
    with patch("builtins.open", mock_open(read_data=_cpuinfo(vendor, family, model, stepping))):
        detected_vendor, processor_id = detect_host_cpu_identity()

    assert detected_vendor == vendor
    assert cpu_fms_from_processor_id(processor_id) == (family, model, stepping)


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

    with patch("subprocess.run", _fake_run(stdout=digest + "\n", capture=cmd)):
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
        compute_snp_measurement(str(image), str(tmp_path / "absent.fd"), 28, GENOA_PROCESSOR_ID)


def test_tool_failure_is_surfaced(tmp_path):
    image = _stage_image(tmp_path)
    firmware = tmp_path / "OVMF.amdsev.fd"
    firmware.write_bytes(b"firmware")

    with patch("subprocess.run", _fake_run(returncode=2, stderr="bad vcpu count")):
        with pytest.raises(MeasurementError, match="bad vcpu count"):
            compute_snp_measurement(str(image), str(firmware), 28, GENOA_PROCESSOR_ID)


def test_unexpected_tool_output_is_refused(tmp_path):
    """A short or non-hex line must not be written into measurements.yaml as if it were
    a measurement."""
    image = _stage_image(tmp_path)
    firmware = tmp_path / "OVMF.amdsev.fd"
    firmware.write_bytes(b"firmware")

    with patch("subprocess.run", _fake_run(stdout="not-a-measurement\n")):
        with pytest.raises(MeasurementError, match="unexpected measurement"):
            compute_snp_measurement(str(image), str(firmware), 28, GENOA_PROCESSOR_ID)


def test_missing_binary_is_reported(tmp_path):
    image = _stage_image(tmp_path)
    firmware = tmp_path / "OVMF.amdsev.fd"
    firmware.write_bytes(b"firmware")

    def raise_missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    with patch("subprocess.run", raise_missing):
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


def _profile_record(name, vendor, *, gpus=("h100",), gpu_count=8, processor_id=GENOA_PROCESSOR_ID):
    """(api record, patched topology_from_profile return) for one host class."""
    from types import SimpleNamespace

    fp = SimpleNamespace(
        cpu=SimpleNamespace(cpu_vendor=vendor, vcpus=28, cpu_processor_id=processor_id),
        gpu=SimpleNamespace(gpu_count=gpu_count, gpu_nodes=()),
        variant_label=name,
    )
    profile = SimpleNamespace(
        display_name=name,
        expected_gpus=list(gpus),
        firmware_filename="OVMF.inteltdx.fd",
    )
    return {"fingerprint": name + "-fp", "profile": {"name": name}}, (profile, fp, "10.2.1")


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


def test_each_host_class_gets_the_measurement_its_cpu_needs(tmp_path):
    """One pass over the published host profiles produces Intel RTMR0 entries and AMD
    launch-digest entries side by side — the vendor on the fingerprint decides which."""
    import chutes_cvm.measurement.generate_measurements as gm

    _stage_image(tmp_path)
    intel_rec, intel_topo = _profile_record("intel-8xh200", "GenuineIntel")
    amd_rec, amd_topo = _profile_record("amd-8xh100", "AuthenticAMD")
    topo = {"intel-8xh200": intel_topo, "amd-8xh100": amd_topo}
    records = [intel_rec, amd_rec]

    with patch.object(gm, "fetch_host_profiles", return_value=records), patch.object(
        gm, "topology_from_profile", side_effect=lambda doc: topo[doc["name"]]
    ), patch.object(
        gm, "generate_acpi_blobs", return_value={"rtmr0": "r0hex", "mrtd": "mrtdhex"}
    ), patch.object(
        # The RTMR0 spec builder wants a full topology; this test is about which
        # measurement each class gets, not about building that spec.
        gm, "build_topology_spec"
    ), patch.object(
        gm, "MeasurementMetadata"
    ), patch.object(
        gm, "compute_snp_measurement", return_value="M" * 96
    ) as snp:
        block = gm._hardware_blocks(_args(tmp_path))

    assert [e["name"] for e in block["tdx_hardware"]] == ["intel-8xh200 [10.2.1, intel-8xh200]"]
    assert [e["name"] for e in block["snp_hardware"]] == ["amd-8xh100 [10.2.1, amd-8xh100]"]
    assert block["tdx_hardware"][0]["rtmr0"] == "R0HEX"
    assert block["snp_hardware"][0]["measurement"] == "M" * 96
    # SNP is measured against the PINNED AMD firmware, not the profile's TDX firmware.
    assert snp.call_args.args[1].endswith("OVMF.amdsev.fd")


def test_rtmr0_partial_skips_amd_classes(tmp_path):
    """`--register rtmr0` is a TDX-only debug partial and has no image to measure SNP
    from, so AMD classes are simply absent rather than pending."""
    import chutes_cvm.measurement.generate_measurements as gm

    amd_rec, amd_topo = _profile_record("amd-8xh100", "AuthenticAMD")

    with patch.object(gm, "fetch_host_profiles", return_value=[amd_rec]), patch.object(
        gm, "topology_from_profile", return_value=amd_topo
    ), patch.object(gm, "compute_snp_measurement") as snp:
        block = gm._hardware_blocks(_args(tmp_path), include_snp=False)

    assert block["snp_hardware"] == []
    assert block["tdx_hardware"] == []
    snp.assert_not_called()


def test_a_class_that_cannot_be_generated_is_pending_not_fatal(tmp_path):
    """An AMD class whose CPU model was never captured must not take the whole release
    down — it is listed pending, exactly as an ungeneratable Intel class is."""
    import chutes_cvm.measurement.generate_measurements as gm

    _stage_image(tmp_path)
    good_rec, good_topo = _profile_record("amd-ok", "AuthenticAMD")
    bad_rec, bad_topo = _profile_record("amd-nocpu", "AuthenticAMD", processor_id=None)
    topo = {"amd-ok": good_topo, "amd-nocpu": bad_topo}

    with patch.object(
        gm, "fetch_host_profiles", return_value=[good_rec, bad_rec]
    ), patch.object(
        gm, "topology_from_profile", side_effect=lambda doc: topo[doc["name"]]
    ), patch.object(
        gm,
        "compute_snp_measurement",
        side_effect=lambda image, fw, vcpus, pid, **kw: (
            "M" * 96 if pid else (_ for _ in ()).throw(MeasurementError("no cpu_processor_id"))
        ),
    ):
        block = gm._hardware_blocks(_args(tmp_path))

    assert [e["name"] for e in block["snp_hardware"]] == ["amd-ok [10.2.1, amd-ok]"]
    assert block["pending_profiles"] == ["amd-nocpu-fp"]
