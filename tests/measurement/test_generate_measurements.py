"""The measurements run: API host classes dispatched to their platforms, the release entry
assembled from both, and the CLI around it.

Each platform's own computation is faked at its edges (the fork, the image, the SEV-SNP inputs);
tests/measurement/test_tdx.py and test_snp.py cover those.
"""

import argparse
import json
from unittest.mock import patch

import pytest
import topology_fixtures as tf
import yaml
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.measurement import generate_measurements as gm
from chutes_cvm.measurement import snp, tdx
from chutes_cvm.measurement.platform import MeasurementError

_H200_DOC = tf.h200_doc(nvswitch_node=0)
_RTX_FLAT_DOC = tf.rtx_flat_doc()


def test_generate_rtmr3_passes_luks_passphrase_from_env(monkeypatch, capsys):
    monkeypatch.setenv("LUKS_PASSPHRASE", "s3cret")
    args = argparse.Namespace(image="img.qcow2", root_part=None)
    seen = {}

    def _fake(image, root_part=None, luks_passphrase=None):
        seen["passphrase"] = luks_passphrase
        return "COMPUTED", [("hash", "/etc/x")]

    with patch(
        "chutes_cvm.measurement.generate_measurements.compute_rtmr3", side_effect=_fake
    ):
        rc = gm._generate_rtmr3(args)
    assert rc == 0
    assert seen["passphrase"] == "s3cret"
    assert capsys.readouterr().out.strip() == "COMPUTED"


def _gen_args(**over):
    args = dict(
        register=None,
        version="1.4.0",
        image="final.qcow2",
        output="-",
        root_part=None,
        profile="",
        qemu="10.2.1",
        tdx_measure_bin="tdx-measure",
        dist="ubuntu:26.04",
        bios_dir="/fw",
        api_base="https://api.example",
        include_pending=False,
    )
    args.update(over)
    return argparse.Namespace(**args)


def _patch_tdx_registers(seen):
    def _fake_r3(image, root_part=None, luks_passphrase=None):
        seen["passphrase"] = luks_passphrase
        return "R3HEX", [("hash", "/etc/x")]

    return (
        patch(
            "chutes_cvm.measurement.tdx.compute_rtmr1_2",
            return_value=("R1HEX", "R2HEX"),
        ),
        patch(
            "chutes_cvm.measurement.tdx.compute_rtmr3",
            side_effect=_fake_r3,
        ),
    )


class _FakeSnpImage:
    def measurement(self, vcpus, processor_id):
        return "M0"


def _intel_class():
    import topology_fixtures as tf

    return {"fingerprint": "a" * 64, "profile": tf.h200_doc(nvswitch_node=0)}


def _amd_class():
    import topology_fixtures as tf

    doc = tf.host_document(
        "RTX_PRO_6000",
        vcpus=124,
        gpu_nodes=(0,) * 8,
        cpu_vendor="AuthenticAMD",
        cpu_processor_id="110fa000fffba91f",
    )
    return {"fingerprint": "b" * 64, "profile": doc}


def _compute(monkeypatch, records, seen=None):
    """Run the whole-release computation with only its I/O edges faked: the API, the fork,
    the image's RTMR1-3 and the SEV-SNP inputs."""
    monkeypatch.setenv("LUKS_PASSPHRASE", "s3cret")
    r12, r3 = _patch_tdx_registers({} if seen is None else seen)
    with patch.object(gm, "fetch_host_profiles", return_value=records), patch.object(
        tdx, "generate_acpi_blobs", return_value={"rtmr0": "r0", "mrtd": "mrtdhex"}
    ), patch.object(snp.SnpImage, "load", return_value=_FakeSnpImage()), r12, r3:
        return gm._compute_measurements(_gen_args())


def test_compute_measurements_assembles_entry(monkeypatch):
    """The pure compute step returns the teeMeasurements entry (no file I/O)."""
    seen = {}
    entry = _compute(monkeypatch, [_intel_class()], seen)

    assert seen["passphrase"] == "s3cret"  # LUKS_PASSPHRASE threaded through to rtmr3
    tdx = entry["tdx"]
    assert tdx["mrtd"] == "MRTDHEX"
    assert tdx["rtmr1"] == "R1HEX"
    assert tdx["rtmr2"] == "R2HEX"
    assert tdx["rtmr3"] == "R3HEX"
    assert tdx["hardware"][0]["rtmr0"] == "R0"
    assert tdx["hardware"][0]["fingerprint"] == "a" * 64
    # No AMD classes registered: no SNP section. The API refuses one with no hardware.
    assert "snp" not in entry
    # Key order matches the chutes-ops values.yaml layout it merges into.
    assert list(entry.keys()) == ["version", "tdx"]
    assert list(tdx.keys()) == ["mrtd", "rtmr1", "rtmr2", "rtmr3", "hardware"]


def test_compute_measurements_puts_each_class_on_its_own_platform(monkeypatch):
    """One image, one pass, both platforms; a class lands in exactly one section, by vendor."""
    entry = _compute(monkeypatch, [_intel_class(), _amd_class()])

    assert [e["fingerprint"] for e in entry["tdx"]["hardware"]] == ["a" * 64]
    assert [e["fingerprint"] for e in entry["snp"]["hardware"]] == ["b" * 64]
    assert entry["tdx"]["hardware"][0]["rtmr0"] == "R0"
    assert "rtmr0" not in entry["snp"]["hardware"][0]
    # SEV-SNP has no version-level values: everything is in each entry's launch digest.
    assert entry["snp"]["hardware"][0]["measurement"] == "M0"
    assert list(entry["snp"].keys()) == ["hardware"]


def test_compute_measurements_writes_no_section_for_a_platform_without_classes(
    monkeypatch,
):
    """With only AMD classes registered there is no TDX section: the API refuses a section with
    no hardware -- and with it the whole config -- and a TDX section needs an MRTD, which only
    the per-class fork runs produce. The TDX registers are still computed from the image, so a
    broken TDX input fails the release either way."""
    entry = _compute(monkeypatch, [_amd_class()])

    assert "tdx" not in entry
    assert list(entry.keys()) == ["version", "snp"]


def test_compute_measurements_refuses_an_empty_result(monkeypatch):
    """Writing a version block with no measurements would publish a release nothing can
    attest against."""
    with pytest.raises(ValueError, match="no measurements generated"):
        _compute(monkeypatch, [])


def test_generate_full_writes_measurements_yaml(tmp_path):
    """A full generate (no --register) serializes the computed entry to --output."""
    out = tmp_path / "measurements.yaml"
    entry = {
        "version": "1.4.0",
        "tdx": {
            "mrtd": "MRTDHEX",
            "rtmr1": "R1HEX",
            "rtmr2": "R2HEX",
            "rtmr3": "R3HEX",
            "hardware": [{"name": "h", "rtmr0": "R0"}],
        },
        "snp": {"hardware": [{"name": "a", "measurement": "M0"}]},
    }
    with patch.object(gm, "_compute_measurements", return_value=entry):
        rc = gm._cmd_generate(_gen_args(output=str(out)))

    assert rc == 0
    doc = yaml.safe_load(out.read_text())
    assert doc["measurements"][0] == entry
    # sort_keys=False preserves the chutes-ops merge layout on disk.
    assert list(doc["measurements"][0].keys()) == ["version", "tdx", "snp"]
    assert list(doc["measurements"][0]["tdx"].keys()) == [
        "mrtd",
        "rtmr1",
        "rtmr2",
        "rtmr3",
        "hardware",
    ]


def test_generate_full_reports_measurement_error(capsys):
    """A MeasurementError from compute surfaces as exit 1, not a traceback."""
    with patch.object(
        gm, "_compute_measurements", side_effect=MeasurementError("boom")
    ):
        rc = gm._cmd_generate(_gen_args())
    assert rc == 1
    assert "boom" in capsys.readouterr().err


def test_generate_register_rtmr3_routes_and_prints_hex(capsys):
    """`generate --register rtmr3` computes only RTMR3 and prints the bare hex to stdout."""

    def _fake_r3(image, root_part=None, luks_passphrase=None):
        return "R3ONLYHEX", [("hash", "/etc/x")]

    with patch(
        "chutes_cvm.measurement.generate_measurements.compute_rtmr3",
        side_effect=_fake_r3,
    ):
        rc = gm._cmd_generate(_gen_args(register="rtmr3"))
    assert rc == 0
    assert capsys.readouterr().out.strip() == "R3ONLYHEX"


def test_generate_register_rtmr3_without_image_is_usage_error(capsys):
    rc = gm._cmd_generate(_gen_args(register="rtmr3", image=None))
    assert rc == 2
    assert "requires --image" in capsys.readouterr().err


def test_generate_full_without_image_is_usage_error(capsys):
    rc = gm._cmd_generate(_gen_args(image=None))
    assert rc == 2
    assert "--image" in capsys.readouterr().err


_H200_DOC = tf.h200_doc(nvswitch_node=0)
_RTX_FLAT_DOC = tf.rtx_flat_doc()


def test_host_profile_reproduces_the_numa_shape_the_host_launches_with():
    """Same values the former hardcoded H200 NVSwitch-node-0 registry entry carried."""
    host = HostProfile(_H200_DOC)
    assert host.gpu_profile.display_name == "8xh200"
    assert host.qemu_version == "10.2.1"
    assert (host.vcpus, host.guest_mem_gb) == (
        124,
        1128,
    )
    assert host.gpu_numa_nodes == (0, 0, 0, 0, 1, 1, 1, 1)
    assert host.nvswitch_numa_nodes == (0, 0, 0, 0)


def test_host_profile_falls_back_to_flat():
    """More than two host NUMA nodes means no guest grouping -- only counts can matter."""
    host = HostProfile(_RTX_FLAT_DOC)
    assert host.gpu_profile.display_name == "8xpro_6000"
    assert host.uses_guest_numa is False
    assert (len(host.gpus), host.guest_mem_gb) == (
        8,
        768,
    )


def test_host_profile_rejects_unknown_device():
    with pytest.raises(ValueError, match="no GPU profile matches"):
        HostProfile(
            tf.host_document("H200", vcpus=124, gpu_nodes=(0,))
            | {"gpus": [dict(tf.h200_doc()["gpus"][0], device_id="dead")]}
        ).gpu_profile


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def _fetch_capturing_url(include_pending):
    payload = [{"fingerprint": "a" * 64, "measured": True, "profile": _H200_DOC}]
    seen = {}

    def _urlopen(req, *a, **k):
        seen["url"] = req.full_url
        return _Resp(payload)

    with patch(
        "chutes_cvm.measurement.generate_measurements.urllib.request.urlopen",
        side_effect=_urlopen,
    ):
        out = gm.fetch_host_profiles(
            "https://api.example", include_pending=include_pending
        )
    return out, seen["url"]


def test_fetch_host_profiles_measured_only_by_default():
    out, url = _fetch_capturing_url(include_pending=False)
    assert out[0]["fingerprint"] == "a" * 64
    assert url.endswith("/servers/tdx/host_profiles")
    assert "include_pending" not in url


def test_fetch_host_profiles_include_pending_adds_query():
    _, url = _fetch_capturing_url(include_pending=True)
    assert url.endswith("/servers/tdx/host_profiles?include_pending=true")


def test_measured_entry_carries_the_api_fingerprint(tdx_platform):
    """The API's fingerprint is stamped onto the generated entry (never recomputed), so the
    reconciler can join the published measurement to the submitted host profile."""
    records = [{"fingerprint": "b" * 64, "profile": _H200_DOC}]
    with patch.object(
        tdx, "generate_acpi_blobs", return_value={"rtmr0": "R0HEX", "mrtd": "MRTDHEX"}
    ):
        pending = gm.measure_host_classes(records, [tdx_platform])
    assert pending == []
    (entry,) = tdx_platform.hardware
    assert entry["fingerprint"] == "b" * 64
    assert entry["rtmr0"] == "R0HEX"
    assert tdx_platform.mrtd == "MRTDHEX"
    assert "8xh200" in entry["name"]


def test_an_unfingerprinted_record_is_pending(tdx_platform):
    records = [{"fingerprint": "", "measured": False, "profile": _H200_DOC}]
    with patch.object(
        tdx, "generate_acpi_blobs", return_value={"rtmr0": "R0", "mrtd": "M"}
    ):
        pending = gm.measure_host_classes(records, [tdx_platform])
    assert tdx_platform.hardware == []
    assert pending == ["<no-fingerprint>"]


# ── pending vs regression ──────────────────────────────────────────────────────


class _SnpImageNeedingAProcessorId:
    """Measures like the real SnpImage, failing the same way for an uncaptured CPU."""

    def measurement(self, vcpus, processor_id):
        if not processor_id:
            raise MeasurementError("fingerprint carries no cpu_processor_id")
        return "S" * 96


def _snp_platform():
    with patch.object(
        snp.SnpImage, "load", return_value=_SnpImageNeedingAProcessorId()
    ):
        return snp.SnpMeasurements(bios_dir="/fw", image="/img/final.qcow2")


def _amd_record(fingerprint="b" * 64, processor_id="110fa000fffba91f", measured=False):
    doc = tf.host_document(
        "RTX_PRO_6000",
        vcpus=124,
        gpu_nodes=(0,) * 8,
        cpu_vendor="AuthenticAMD",
        cpu_processor_id=processor_id,
    )
    return {"fingerprint": fingerprint, "measured": measured, "profile": doc}


def test_each_class_lands_on_its_own_platform(tdx_platform):
    platform = _snp_platform()
    records = [
        {"fingerprint": "a" * 64, "profile": _H200_DOC},
        _amd_record(),
    ]
    with patch.object(
        tdx, "generate_acpi_blobs", return_value={"rtmr0": "R0", "mrtd": "M"}
    ):
        pending = gm.measure_host_classes(records, [tdx_platform, platform])
    assert pending == []
    assert [e["fingerprint"] for e in tdx_platform.hardware] == ["a" * 64]
    assert [e["fingerprint"] for e in platform.hardware] == ["b" * 64]


def test_a_missing_snp_input_fails_the_release_not_the_class(monkeypatch):
    """A missing firmware or staged artifact is the same for every AMD class. As a per-class
    PENDING it would publish a mixed release without its AMD measurements and exit 0."""
    monkeypatch.setenv("LUKS_PASSPHRASE", "x")
    with patch.object(tdx, "compute_rtmr1_2", return_value=("R1", "R2")), patch.object(
        tdx, "compute_rtmr3", return_value=("R3", [])
    ), patch.object(
        snp.SnpImage, "load", side_effect=MeasurementError("no firmware")
    ), patch.object(
        gm, "fetch_host_profiles", side_effect=AssertionError("measured classes")
    ):
        with pytest.raises(MeasurementError, match="no firmware"):
            gm._compute_measurements(_gen_args())


def test_a_never_measured_class_that_cannot_generate_stays_pending():
    """A new class missing an input (here an uncaptured CPU) waits in the queue; the rest of
    the release still generates."""
    platform = _snp_platform()
    records = [_amd_record(), _amd_record(fingerprint="c" * 64, processor_id=None)]
    pending = gm.measure_host_classes(records, [platform])
    assert [e["fingerprint"] for e in platform.hardware] == ["b" * 64]
    assert pending == ["c" * 64]


def test_a_previously_measured_class_that_fails_is_a_regression():
    """Hosts of a measured class attest today; publishing a release without it would leave them
    nothing to attest against. Every such class is reported, then the run fails."""
    records = [
        _amd_record(fingerprint="c" * 64, processor_id=None, measured=True),
        _amd_record(fingerprint="d" * 64, processor_id=None, measured=True),
        _amd_record(fingerprint="e" * 64, processor_id=None, measured=False),
    ]
    with pytest.raises(ValueError, match="2 previously measured") as err:
        gm.measure_host_classes(records, [_snp_platform()])
    assert "cccccccccccc" in str(err.value) and "dddddddddddd" in str(err.value)
    assert "eeeeeeeeeeee" not in str(
        err.value
    )  # never measured: pending, not a regression


def test_a_measured_class_with_no_platform_generating_it_fails_the_run(tdx_platform):
    """A class is never silently left out of a release."""
    with pytest.raises(ValueError, match="SnpTeeProvider"):
        gm.measure_host_classes([_amd_record(measured=True)], [tdx_platform])


def test_a_new_class_with_no_platform_generating_it_is_pending(tdx_platform):
    pending = gm.measure_host_classes([_amd_record(measured=False)], [tdx_platform])
    assert tdx_platform.hardware == []
    assert pending == ["b" * 64]
