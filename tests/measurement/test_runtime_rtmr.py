"""Tests for the version-level runtime RTMRs (RTMR1/RTMR2/RTMR3).

Covers the pure/host-independent logic — the RTMR3 extension chain and file selection, and
RTMR1/RTMR2 parsing — plus the `build` command's measurements.yaml assembly. The guestmount /
tdx-measure subprocesses are mocked; the SHA-384 math is real.
"""

import argparse
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from chutes_cvm.measurement import generate_measurements as gm
from chutes_cvm.measurement import runtime_rtmr as rr

# ── RTMR3 chain ────────────────────────────────────────────────────────────────


def test_rtmr3_chain_matches_reference(tmp_path):
    a = tmp_path / "a"
    a.write_bytes(b"alpha")
    b = tmp_path / "b"
    b.write_bytes(b"beta")
    files = [("/etc/a", str(a)), ("/etc/b", str(b))]

    rtmr3, per_file = rr.rtmr3_chain(files)

    # Independent reference: rtmr3 = 0x00*48; rtmr3 = SHA384(rtmr3 || SHA384(contents)).
    acc = bytes(48)
    for _, full in files:
        acc = hashlib.sha384(
            acc + hashlib.sha384(Path(full).read_bytes()).digest()
        ).digest()
    assert rtmr3 == acc.hex().upper()
    assert [p[1] for p in per_file] == ["/etc/a", "/etc/b"]
    assert per_file[0][0] == hashlib.sha384(b"alpha").hexdigest()


def test_rtmr3_chain_is_order_sensitive(tmp_path):
    a = tmp_path / "a"
    a.write_bytes(b"x")
    b = tmp_path / "b"
    b.write_bytes(b"y")
    r1, _ = rr.rtmr3_chain([("/a", str(a)), ("/b", str(b))])
    r2, _ = rr.rtmr3_chain([("/b", str(b)), ("/a", str(a))])
    assert r1 != r2


def test_measured_files_sorts_filters_and_strips_comments(tmp_path):
    root = tmp_path / "root"
    (root / "etc/ssh").mkdir(parents=True)
    (root / "etc/ssh/sshd_config").write_text("cfg")
    (root / "etc/hostname").write_text("h")
    (root / "etc/link").symlink_to(root / "etc/hostname")  # symlink must be skipped
    conf = tmp_path / "conf"
    conf.write_text("/etc/ssh\n/etc/hostname\n# a comment\n\n")

    entries = rr._measured_files(str(root), str(conf))
    rels = [e[0] for e in entries]

    assert rels == sorted(rels)  # sorted by root-relative path
    assert "/etc/hostname" in rels
    assert "/etc/ssh/sshd_config" in rels
    assert "/etc/link" not in rels  # symlink filtered out


def test_measured_files_empty_conf_raises(tmp_path):
    conf = tmp_path / "conf"
    conf.write_text("# only comments\n\n")
    with pytest.raises(rr.MeasurementError, match="no paths configured"):
        rr._measured_files(str(tmp_path), str(conf))


# ── RTMR1 / RTMR2 ──────────────────────────────────────────────────────────────


def _stage_artifacts(tmp_path):
    base = tmp_path / "img"
    (tmp_path / "img.vmlinuz").write_bytes(b"k")
    (tmp_path / "img.initrd").write_bytes(b"i")
    (tmp_path / "img.cmdline").write_text("console=ttyS0\n")
    return str(base) + ".qcow2"


def test_compute_rtmr1_2_parses_and_uppercases(tmp_path):
    image = _stage_artifacts(tmp_path)
    fake = MagicMock(returncode=0, stdout="RTMR1: abcdef\nRTMR2: 012ABC\n", stderr="")
    with patch("chutes_cvm.measurement.runtime_rtmr.subprocess.run", return_value=fake):
        r1, r2 = rr.compute_rtmr1_2(image)
    assert r1 == "ABCDEF"
    assert r2 == "012ABC"


def test_compute_rtmr1_2_missing_artifact_raises(tmp_path):
    with pytest.raises(rr.MeasurementError, match="missing direct-boot artifact"):
        rr.compute_rtmr1_2(str(tmp_path / "none.qcow2"))


def test_compute_rtmr1_2_unparseable_output_raises(tmp_path):
    image = _stage_artifacts(tmp_path)
    fake = MagicMock(returncode=0, stdout="nothing useful here\n", stderr="")
    with patch("chutes_cvm.measurement.runtime_rtmr.subprocess.run", return_value=fake):
        with pytest.raises(rr.MeasurementError, match="could not parse"):
            rr.compute_rtmr1_2(image)


# ── RTMR3 LUKS handling (always fresh; unlock with LUKS_PASSPHRASE) ─────────────


def test_root_is_luks_detects_encrypted():
    enc = MagicMock(returncode=0, stdout="/dev/sda2: crypto_LUKS\n", stderr="")
    pt = MagicMock(returncode=0, stdout="/dev/sda2: ext4\n", stderr="")
    with patch("chutes_cvm.measurement.runtime_rtmr.subprocess.run", return_value=enc):
        assert rr.root_is_luks("x.qcow2") is True
    with patch("chutes_cvm.measurement.runtime_rtmr.subprocess.run", return_value=pt):
        assert rr.root_is_luks("x.qcow2") is False


def test_compute_rtmr3_luks_without_passphrase_raises(tmp_path):
    img = tmp_path / "enc.qcow2"
    img.write_bytes(b"x")
    with patch("chutes_cvm.measurement.runtime_rtmr._have", return_value=True), patch(
        "chutes_cvm.measurement.runtime_rtmr.root_is_luks", return_value=True
    ):
        with pytest.raises(rr.MeasurementError, match="LUKS_PASSPHRASE"):
            rr.compute_rtmr3(str(img))


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


# ── `generate` command → measurements.yaml (compute + write + routing) ──────────


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
    )
    args.update(over)
    return argparse.Namespace(**args)


def test_compute_measurements_assembles_entry(monkeypatch):
    """The pure compute step returns the teeMeasurements entry (no file I/O)."""
    monkeypatch.setenv("LUKS_PASSPHRASE", "s3cret")
    block = {
        "version": "1.4.0",
        "mrtd": "MRTDHEX",
        "hardware": [{"name": "h", "rtmr0": "R0"}],
    }
    seen = {}

    def _fake_r3(image, root_part=None, luks_passphrase=None):
        seen["passphrase"] = luks_passphrase
        return "R3HEX", [("hash", "/etc/x")]

    with patch.object(gm, "_rtmr0_block", return_value=block), patch(
        "chutes_cvm.measurement.generate_measurements.compute_rtmr1_2",
        return_value=("R1HEX", "R2HEX"),
    ), patch(
        "chutes_cvm.measurement.generate_measurements.compute_rtmr3",
        side_effect=_fake_r3,
    ):
        entry = gm._compute_measurements(_gen_args())

    assert seen["passphrase"] == "s3cret"  # LUKS_PASSPHRASE threaded through to rtmr3
    assert entry["mrtd"] == "MRTDHEX"
    assert entry["rtmr1"] == "R1HEX"
    assert entry["rtmr2"] == "R2HEX"
    assert entry["runtime_rtmr3"] == "R3HEX"
    assert entry["hardware"][0]["rtmr0"] == "R0"
    # Key order matches the chutes-ops values.yaml layout it merges into.
    assert list(entry.keys()) == [
        "version",
        "mrtd",
        "rtmr1",
        "rtmr2",
        "runtime_rtmr3",
        "hardware",
    ]


def test_generate_full_writes_measurements_yaml(tmp_path):
    """A full generate (no --register) serializes the computed entry to --output."""
    out = tmp_path / "measurements.yaml"
    entry = {
        "version": "1.4.0",
        "mrtd": "MRTDHEX",
        "rtmr1": "R1HEX",
        "rtmr2": "R2HEX",
        "runtime_rtmr3": "R3HEX",
        "hardware": [{"name": "h", "rtmr0": "R0"}],
    }
    with patch.object(gm, "_compute_measurements", return_value=entry):
        rc = gm._cmd_generate(_gen_args(output=str(out)))

    assert rc == 0
    doc = yaml.safe_load(out.read_text())
    assert doc["measurements"][0] == entry
    # sort_keys=False preserves the chutes-ops merge layout on disk.
    assert list(doc["measurements"][0].keys()) == [
        "version",
        "mrtd",
        "rtmr1",
        "rtmr2",
        "runtime_rtmr3",
        "hardware",
    ]


def test_generate_full_reports_measurement_error(capsys):
    """A MeasurementError from compute surfaces as exit 1, not a traceback."""
    with patch.object(
        gm, "_compute_measurements", side_effect=rr.MeasurementError("boom")
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
