"""Tests for chutes_cvm.guest.host_class -- this host's class as Chutes records it."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import topology_fixtures as known
from chutes_cvm.guest.host_class import (
    MeasuredImage,
    HostClass,
    HostClassStatus,
    NotMeasured,
    SnpMeasuredImage,
    SnpHostClass,
    TdxHostClass,
)
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.chutes_api import ChutesApiError

_ACPI = "6a8501a0f92db861ac1f055a4015adc52662da7b760463ec0331e40bb1f5f5a7"


def _intel():
    return HostProfile.from_dict(known.rtx_numa_doc())


def _amd():
    doc = known.rtx_numa_doc()
    doc["cpu"]["vendor"] = "AuthenticAMD"
    return HostProfile.from_dict(doc)


def _response(*measurements, status="accepted"):
    return {
        "fingerprint": "fp",
        "status": status,
        "measurements": list(measurements),
        "detail": "d",
    }


def test_an_intel_class_parses_tdx_entries():
    host_class = TdxHostClass.from_response(_response({"version": "1.5.0", "rc": False}))
    assert host_class.fingerprint == "fp"
    assert host_class.status is HostClassStatus.ACCEPTED
    assert host_class.measured == [
        MeasuredImage(version="1.5.0", rc=False)
    ]


def test_an_amd_class_carries_each_entry_s_acpi_hash():
    host_class = SnpHostClass.from_response(_response({"version": "1.5.0", "rc": True, "acpi_sha256": _ACPI}))
    assert host_class.measured == [
        SnpMeasuredImage(version="1.5.0", rc=True, acpi_sha256=_ACPI)
    ]


@pytest.mark.parametrize("acpi", [None, "", "unverified", "A" * 64, "a" * 63])
def test_an_snp_entry_without_a_real_acpi_hash_fails_at_retrieval(acpi):
    """The SNP firmware cannot boot without the hash, so a class missing it fails here, where
    the API's answer is read, not at launch."""
    entry = {"version": "1.5.0", "rc": False}
    if acpi is not None:
        entry["acpi_sha256"] = acpi
    with pytest.raises(ChutesApiError, match="without a valid acpi_sha256"):
        SnpHostClass.from_response(_response(entry))


def test_an_entry_without_a_version_is_refused():
    with pytest.raises(ChutesApiError, match="no version"):
        TdxHostClass.from_response(_response({"rc": False}))


def test_an_unknown_status_is_refused():
    with pytest.raises(ChutesApiError, match="unknown host class status"):
        TdxHostClass.from_response(_response(status="retired"))


def test_a_class_with_nothing_published_covers_nothing():
    host_class = TdxHostClass.from_response(_response(status="pending"))
    assert host_class.status is HostClassStatus.PENDING
    assert host_class.measured == []


def test_an_image_s_entry_is_found_by_version_and_rc():
    host_class = TdxHostClass.from_response(_response({"version": "1.5.0", "rc": False}, {"version": "1.5.0", "rc": True}))
    found = host_class.measured_image(known.fake_image_set("1.5.0", rc=True))
    assert (found.version, found.rc, found.label) == ("1.5.0", True, "1.5.0 (rc)")


def test_an_unpublished_image_is_not_measured():
    host_class = TdxHostClass.from_response(_response({"version": "1.5.0", "rc": False}))
    debug = known.fake_image_set("1.5.0", rc=True)
    with pytest.raises(NotMeasured, match=r"1\.5\.0 \(rc\)") as raised:
        host_class.measured_image(debug)
    assert raised.value.image is debug


@pytest.mark.parametrize(
    ("host", "class_type"), [(_intel, TdxHostClass), (_amd, SnpHostClass)]
)
def test_each_platform_parses_its_own_class(host, class_type):
    assert HostClass.type_for(host()) is class_type


def test_a_platform_without_a_class_type_is_refused():
    other = SimpleNamespace(tee_provider=SimpleNamespace(label="Other TEE"))
    with pytest.raises(ValueError, match="no HostClass for Other TEE"):
        HostClass.type_for(other)


def test_fetch_signs_the_given_profile_and_parses_the_answer():
    """The class is about the profile the caller holds -- the one a launch boots -- not a
    second reading of the host."""
    host = _amd()
    with patch(
        "chutes_cvm.guest.host_class.host_class_status",
        return_value=_response({"version": "1.5.0", "rc": False, "acpi_sha256": _ACPI}),
    ) as status:
        host_class = HostClass.fetch(
            host, config_path="/c.yaml", api_base="https://api", target_os="26.04"
        )
    assert isinstance(host_class, SnpHostClass)
    status.assert_called_once_with(
        config_path="/c.yaml", host_profile=host, api_base="https://api", target_os="26.04"
    )
    assert host_class.measured_image(known.fake_image_set("1.5.0")).acpi_sha256 == _ACPI
