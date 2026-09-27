"""TDX measurements: RTMR1/2 from the fork, RTMR3 from the mounted image, MRTD across classes.

The qemu-nbd / cryptsetup / tdx-measure subprocesses are mocked; the SHA-384 math is real.
"""

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import topology_fixtures as tf
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.measurement import tdx
from chutes_cvm.measurement.platform import MeasurementError

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
    with patch("chutes_cvm.measurement.tdx.proc.run", return_value=fake):
        r1, r2 = tdx.compute_rtmr1_2(image)
    assert r1 == "ABCDEF"
    assert r2 == "012ABC"


def test_compute_rtmr1_2_missing_artifact_raises(tmp_path):
    with pytest.raises(MeasurementError, match="direct-boot artifacts missing"):
        tdx.compute_rtmr1_2(str(tmp_path / "none.qcow2"))


def test_compute_rtmr1_2_unparseable_output_raises(tmp_path):
    image = _stage_artifacts(tmp_path)
    fake = MagicMock(returncode=0, stdout="nothing useful here\n", stderr="")
    with patch("chutes_cvm.measurement.tdx.proc.run", return_value=fake):
        with pytest.raises(MeasurementError, match="could not parse"):
            tdx.compute_rtmr1_2(image)


def test_compute_rtmr1_2_metadata_paths_are_absolute(tmp_path, monkeypatch):
    """A relative --image must yield ABSOLUTE kernel/initrd paths in the tdx-measure metadata:
    the fork resolves them relative to the metadata file (a temp dir), not the caller's cwd.
    """
    _stage_artifacts(tmp_path)  # stages img.{vmlinuz,initrd,cmdline} under tmp_path
    monkeypatch.chdir(tmp_path)
    captured = {}

    def _fake_run(cmd, **kwargs):
        # cmd = [tdx-measure, --runtime-only, <meta_path>]; read what got written.
        captured.update(json.loads(Path(cmd[2]).read_text())["direct"])
        return MagicMock(returncode=0, stdout="RTMR1: aa\nRTMR2: bb\n", stderr="")

    with patch("chutes_cvm.measurement.tdx.proc.run", side_effect=_fake_run):
        tdx.compute_rtmr1_2("img.qcow2")  # relative path

    assert os.path.isabs(captured["kernel"])
    assert captured["kernel"] == str(tmp_path / "img.vmlinuz")
    assert captured["initrd"] == str(tmp_path / "img.initrd")


# ── RTMR3 LUKS handling (always fresh; unlock with LUKS_PASSPHRASE) ─────────────


def _blkid_by_part(types: dict):
    """proc.run side_effect returning the blkid TYPE for the partition in argv."""

    def _run(argv, *a, **k):
        part = argv[-1]  # blkid -o value -s TYPE <part>
        return MagicMock(returncode=0, stdout=types.get(part, "") + "\n", stderr="")

    return _run


def test_detect_root_partition_prefers_luks():
    parts = ["/dev/nbd0p1", "/dev/nbd0p16"]
    types = {"/dev/nbd0p1": "crypto_LUKS", "/dev/nbd0p16": "ext4"}
    with patch("chutes_cvm.measurement.tdx.glob.glob", return_value=parts), patch(
        "chutes_cvm.measurement.tdx.proc.run",
        side_effect=_blkid_by_part(types),
    ):
        assert tdx._detect_root_partition("/dev/nbd0") == ("/dev/nbd0p1", True)


def test_detect_root_partition_plaintext_ext4():
    # No LUKS: the ext4 root is returned, is_luks False (sysfs size read is absent in tests -> 0).
    parts = ["/dev/nbd0p1", "/dev/nbd0p15"]
    types = {"/dev/nbd0p1": "ext4", "/dev/nbd0p15": "vfat"}
    with patch("chutes_cvm.measurement.tdx.glob.glob", return_value=parts), patch(
        "chutes_cvm.measurement.tdx.proc.run",
        side_effect=_blkid_by_part(types),
    ):
        dev, is_luks = tdx._detect_root_partition("/dev/nbd0")
        assert dev == "/dev/nbd0p1"
        assert is_luks is False


def test_detect_root_partition_no_root_raises():
    with patch(
        "chutes_cvm.measurement.tdx.glob.glob", return_value=["/dev/nbd0p15"]
    ), patch(
        "chutes_cvm.measurement.tdx.proc.run",
        side_effect=_blkid_by_part({"/dev/nbd0p15": "vfat"}),
    ):
        with pytest.raises(MeasurementError, match="no ext4 or LUKS root"):
            tdx._detect_root_partition("/dev/nbd0")


def test_compute_rtmr3_luks_without_passphrase_raises(tmp_path):
    # An encrypted root with no LUKS_PASSPHRASE fails closed (never mounts the wrong partition).
    img = tmp_path / "enc.qcow2"
    img.write_bytes(b"x")
    ok = MagicMock(returncode=0, stdout="", stderr="")
    with patch("chutes_cvm.measurement.tdx.os.geteuid", return_value=0), patch(
        "chutes_cvm.measurement.tdx._have", return_value=True
    ), patch(
        "chutes_cvm.measurement.tdx._free_nbd_device", return_value="/dev/nbd0"
    ), patch(
        "chutes_cvm.measurement.tdx._wait_for_path", return_value=True
    ), patch(
        "chutes_cvm.measurement.tdx._detect_root_partition",
        return_value=("/dev/nbd0p1", True),
    ), patch(
        "chutes_cvm.measurement.tdx.proc.run", return_value=ok
    ):
        with pytest.raises(MeasurementError, match="LUKS_PASSPHRASE"):
            tdx.compute_rtmr3(str(img))


def test_compute_rtmr3_plaintext_needs_no_cryptsetup(tmp_path):
    # A plaintext (debug) root mounts and measures with cryptsetup absent — same process as prod,
    # minus the luksOpen. Locks in that the debug measurement path is unaffected.
    img = tmp_path / "debug.qcow2"
    img.write_bytes(b"x")
    root = tmp_path / "mnt"
    (root / "etc").mkdir(parents=True)
    (root / "etc/tdx-measure.conf").write_text("/etc/hostname\n")
    (root / "etc/hostname").write_text("h")

    ok = MagicMock(returncode=0, stdout="", stderr="")
    with patch("chutes_cvm.measurement.tdx.os.geteuid", return_value=0), patch(
        "chutes_cvm.measurement.tdx._have",
        side_effect=lambda t: t != "cryptsetup",
    ), patch(
        "chutes_cvm.measurement.tdx._free_nbd_device", return_value="/dev/nbd0"
    ), patch(
        "chutes_cvm.measurement.tdx._wait_for_path", return_value=True
    ), patch(
        "chutes_cvm.measurement.tdx._detect_root_partition",
        return_value=("/dev/nbd0p1", False),
    ), patch(
        "chutes_cvm.measurement.tdx.tempfile.mkdtemp", return_value=str(root)
    ), patch(
        "chutes_cvm.measurement.tdx.proc.run", return_value=ok
    ):
        rtmr3, per_file = tdx.compute_rtmr3(str(img))

    assert len(rtmr3) == 96  # SHA-384 hex, uppercase
    assert per_file == [(hashlib.sha384(b"h").hexdigest(), "/etc/hostname")]


# ── MRTD (per class, from the same fork runs as RTMR0) ─────────────────────────


def test_mrtd_must_agree_across_topologies(tdx_platform):
    """One TDVF measures identically on every topology of a build; disagreement is corruption."""
    mrtds = iter(["AAAA", "BBBB"])
    hosts = [
        HostProfile.from_api_profile(tf.h200_doc(nvswitch_node=0)),
        HostProfile.from_api_profile(tf.rtx_flat_doc()),
    ]
    with patch.object(
        tdx,
        "generate_acpi_blobs",
        side_effect=lambda *a, **k: {"rtmr0": "R0", "mrtd": next(mrtds)},
    ):
        for i, host in enumerate(hosts):
            tdx_platform.add(host, str(i) * 64)
    with pytest.raises(ValueError, match="MRTD differs"):
        tdx_platform.mrtd


def test_rtmr0_and_mrtd_come_from_one_fork_run_per_class(tdx_platform):
    with patch.object(
        tdx, "generate_acpi_blobs", return_value={"rtmr0": "r0hex", "mrtd": "mrtdhex"}
    ) as fork:
        entry = tdx_platform.add(HostProfile.from_api_profile(tf.h200_doc()), "a" * 64)
    fork.assert_called_once()
    assert entry["rtmr0"] == "R0HEX"
    assert tdx_platform.section() == {
        "mrtd": "MRTDHEX",
        "rtmr1": "R1",
        "rtmr2": "R2",
        "rtmr3": "R3",
        "hardware": [entry],
    }
