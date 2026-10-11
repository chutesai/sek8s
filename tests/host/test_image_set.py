"""Tests for chutes_cvm.guest.image_set.ImageSet -- a base image set on disk."""

import hashlib
import json

import pytest
from chutes_cvm.guest.image_set import FIRMWARE, ImageSet, write_manifest
from chutes_cvm.paths import firmware_dir


def _stage(tmp_path, *, version="1.4.0", debug=False):
    """A complete set as the build writes it; returns its directory."""
    qcow2 = tmp_path / "tdx-guest.qcow2"
    qcow2.write_bytes(b"qcow2-bytes")
    for role in ("vmlinuz", "initrd", "cmdline"):
        (tmp_path / f"tdx-guest.{role}").write_bytes(role.encode())
    write_manifest(
        str(qcow2), str(tmp_path / "manifest.json"), version=version, debug=debug
    )
    return str(tmp_path)


def test_a_set_says_which_build_it_is(tmp_path):
    image = ImageSet.from_dir(_stage(tmp_path, version="1.5.0", debug=True))
    assert (image.version, image.rc, image.label) == ("1.5.0", True, "1.5.0 (rc)")
    assert image.qcow2.path == str(tmp_path / "tdx-guest.qcow2")
    assert image.cmdline.path == str(tmp_path / "tdx-guest.cmdline")


def test_a_production_build_is_not_rc(tmp_path):
    image = ImageSet.from_dir(_stage(tmp_path))
    assert image.rc is False and image.label == "1.4.0"


def test_a_manifest_without_a_version_is_refused(tmp_path):
    with pytest.raises(ValueError, match="no version"):
        ImageSet.from_dir(_stage(tmp_path, version=""))


def test_a_set_without_a_manifest_is_refused(tmp_path):
    _stage(tmp_path)
    (tmp_path / "manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="manifest.json missing"):
        ImageSet.from_dir(str(tmp_path))


def test_a_manifest_missing_a_role_is_refused(tmp_path):
    _stage(tmp_path)
    manifest = tmp_path / "manifest.json"
    data = json.loads(manifest.read_text())
    del data["artifacts"]["initrd"]
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="missing artifact roles"):
        ImageSet.from_dir(str(tmp_path))


def test_an_intact_set_verifies_fully(tmp_path):
    ImageSet.from_dir(_stage(tmp_path)).verify(full=True)


def test_verify_reports_every_mismatch(tmp_path):
    image = ImageSet.from_dir(_stage(tmp_path))
    (tmp_path / "tdx-guest.vmlinuz").unlink()
    (tmp_path / "tdx-guest.initrd").write_bytes(b"much longer than before")
    with pytest.raises(ValueError) as raised:
        image.verify(full=False)
    assert "missing vmlinuz" in str(raised.value)
    assert "initrd size mismatch" in str(raised.value)


def test_only_a_full_verify_rehashes(tmp_path):
    """Launch checks presence and size; download re-hashes. Same-size corruption is caught
    only by the full check."""
    image = ImageSet.from_dir(_stage(tmp_path))
    (tmp_path / "tdx-guest.cmdline").write_bytes(b"CMDLINE")  # same length as "cmdline"
    image.verify(full=False)
    with pytest.raises(ValueError, match="cmdline sha256 mismatch"):
        image.verify(full=True)


# ── the firmware the image was built with ───────────────────────────────────────────────────


def _digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_the_manifest_records_the_firmware_the_image_was_built_with(tmp_path):
    image = ImageSet.from_dir(_stage(tmp_path))
    assert image.firmware == {name: _digest(firmware_dir() / name) for name in FIRMWARE}


def test_the_firmware_the_image_was_built_with_verifies(tmp_path):
    ImageSet.from_dir(_stage(tmp_path)).verify(full=True)


@pytest.mark.parametrize("name", FIRMWARE)
def test_any_other_firmware_fails_verification(tmp_path, name):
    """The firmware ships with chutes-cvm, not the image, so it is checked on every verify."""
    image = ImageSet.from_dir(_stage(tmp_path))
    other = tmp_path / "other"
    other.mkdir()
    for fw in FIRMWARE:
        (other / fw).write_bytes((firmware_dir() / fw).read_bytes())
    (other / name).write_bytes(b"retired firmware")
    with pytest.raises(ValueError, match="was built with"):
        image.verify(full=False, firmware=str(other))


def test_a_set_from_before_firmware_was_recorded_is_not_checked(tmp_path):
    directory = _stage(tmp_path)
    manifest = tmp_path / "manifest.json"
    doc = json.loads(manifest.read_text())
    del doc["firmware"]
    manifest.write_text(json.dumps(doc))
    ImageSet.from_dir(directory).verify(full=True, firmware=str(tmp_path / "nowhere"))
