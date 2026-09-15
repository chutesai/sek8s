"""Hardware-entry uniqueness rules in the measurement generator.

A host class is identified by its FINGERPRINT. Whenever the fingerprint's inputs change, every
class re-registers under a new fingerprint while the old record stays live (hosts upgrade at
different times), so one topology legitimately has several fingerprints that all resolve to the
same display name. Failing the build on that stalls every release behind a fingerprint schema
change. The collision worth catching is two genuinely different topologies landing on one name,
which shows up as the same name carrying different rtmr0 values.
"""

from __future__ import annotations

import pytest
from chutes_cvm.measurement.generate_measurements import resolve_hardware_names


def _entry(name: str, fingerprint: str, rtmr0: str) -> dict:
    return {"name": name, "fingerprint": fingerprint, "rtmr0": rtmr0}


def test_same_topology_two_fingerprints_is_allowed_and_disambiguated():
    """The real-world case: one host class re-registered under a new fingerprint."""
    hardware = [
        _entry("8xh200 [10.2.1, numa-124c]", "09496918a9bb32ef", "5B509103A3BF3C10"),
        _entry("8xh200 [10.2.1, numa-124c]", "ba5d6f015012c674", "5B509103A3BF3C10"),
    ]
    resolve_hardware_names(hardware)

    assert len({e["name"] for e in hardware}) == 2, "labels must be disambiguated"
    for e in hardware:
        assert e["fingerprint"][:12] in e["name"]
    # Both entries survive — dropping one would strand whichever hosts present that fingerprint.
    assert len(hardware) == 2


def test_distinct_names_are_left_untouched():
    hardware = [
        _entry("8xh200 [10.2.1, numa-124c]", "aaaaaaaaaaaa1111", "5B509103A3BF3C10"),
        _entry(
            "8xpro_6000 [10.2.1, numa-124c]", "bbbbbbbbbbbb2222", "5E939A6213A6751F"
        ),
    ]
    resolve_hardware_names(hardware)
    assert [e["name"] for e in hardware] == [
        "8xh200 [10.2.1, numa-124c]",
        "8xpro_6000 [10.2.1, numa-124c]",
    ]


def test_same_name_differing_rtmr0_is_not_an_error():
    """The name is a strict subset of the rtmr0 inputs -- cpu_vendor / phys_bits /
    cpu_processor_id all move rtmr0 and appear in none of display_name, qemu or variant_label.
    So a shared name with differing rtmr0 means the label is coarser than the measurement, not
    that anything is corrupt. Each entry keeps its own rtmr0."""
    hardware = [
        _entry("8xh200 [10.2.1, numa-124c]", "aaaaaaaaaaaa1111", "5B509103A3BF3C10"),
        _entry("8xh200 [10.2.1, numa-124c]", "bbbbbbbbbbbb2222", "DEADBEEFDEADBEEF"),
    ]
    resolve_hardware_names(hardware)

    assert len({e["name"] for e in hardware}) == 2
    assert [e["rtmr0"] for e in hardware] == ["5B509103A3BF3C10", "DEADBEEFDEADBEEF"]


def test_duplicate_fingerprint_is_fatal():
    """The fingerprint is the actual key; the same one twice is corrupt input."""
    hardware = [
        _entry("8xh200 [10.2.1, numa-124c]", "aaaaaaaaaaaa1111", "5B509103A3BF3C10"),
        _entry("8xh200 [10.2.1, numa-124c]", "aaaaaaaaaaaa1111", "5B509103A3BF3C10"),
    ]
    with pytest.raises(ValueError, match="duplicate host-profile fingerprints"):
        resolve_hardware_names(hardware)
