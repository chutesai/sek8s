"""Hardware-entry identity rules in the measurement generator.

A host class is identified by its FINGERPRINT; classes that measure identically share one
entry listing all their fingerprints (the API refuses a measurement on two entries). Names are
only labels: built from a strict subset of what feeds rtmr0, two different measurements can
land on one name, which is disambiguated rather than refused.
"""

from __future__ import annotations

import pytest
from chutes_cvm.measurement.generate_measurements import resolve_hardware_names


def _entry(name: str, rtmr0: str, *fingerprints: str) -> dict:
    return {"name": name, "fingerprints": list(fingerprints), "rtmr0": rtmr0}


def test_distinct_names_are_left_untouched():
    hardware = [
        _entry("8xh200 [10.2.1, numa-124c]", "5B509103A3BF3C10", "aaaaaaaaaaaa1111"),
        _entry(
            "8xpro_6000 [10.2.1, numa-124c]", "5E939A6213A6751F", "bbbbbbbbbbbb2222"
        ),
    ]
    resolve_hardware_names(hardware)
    assert [e["name"] for e in hardware] == [
        "8xh200 [10.2.1, numa-124c]",
        "8xpro_6000 [10.2.1, numa-124c]",
    ]


def test_same_name_differing_rtmr0_is_disambiguated_not_refused():
    """cpu_vendor / phys_bits / cpu_processor_id all move rtmr0 and appear in none of
    display_name, qemu or variant_label, so a shared name means the label is coarser than the
    measurement, not that anything is corrupt. Each entry keeps its own rtmr0."""
    hardware = [
        _entry(
            "8xh200 [10.2.1, numa-124c]", "5B509103A3BF3C10", "aaaaaaaaaaaa1111", "cccc"
        ),
        _entry("8xh200 [10.2.1, numa-124c]", "DEADBEEFDEADBEEF", "bbbbbbbbbbbb2222"),
    ]
    resolve_hardware_names(hardware)

    assert [e["name"] for e in hardware] == [
        "8xh200 [10.2.1, numa-124c] (aaaaaaaaaaaa)",
        "8xh200 [10.2.1, numa-124c] (bbbbbbbbbbbb)",
    ]
    assert [e["rtmr0"] for e in hardware] == ["5B509103A3BF3C10", "DEADBEEFDEADBEEF"]


@pytest.mark.parametrize(
    "hardware",
    [
        [_entry("a", "R0", "aaaa"), _entry("b", "R1", "aaaa")],
        [_entry("a", "R0", "aaaa", "aaaa")],
    ],
    ids=["two-entries", "one-entry"],
)
def test_a_fingerprint_listed_twice_is_fatal(hardware):
    """The fingerprint is the actual key; the same one twice is corrupt input."""
    with pytest.raises(ValueError, match="listed more than once"):
        resolve_hardware_names(hardware)
