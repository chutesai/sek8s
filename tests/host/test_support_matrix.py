"""Tests for lab-validated host topology matrix."""

import chutes.host.support_matrix as support_matrix
from chutes.host.support_matrix import (
    format_topology_matrix,
    is_validated_topology,
    validated_topology_rows,
)


def test_h200_8_on_2510_validated():
    assert is_validated_topology("25.10", "H200", 8)


def test_h200_8_on_2604_validated():
    assert is_validated_topology("26.04", "H200", 8)


def test_rtx_pro_8_on_2510_validated():
    assert is_validated_topology("25.10", "RTX_PRO_6000", 8)


def test_rtx_pro_8_on_2604_validated():
    assert is_validated_topology("26.04", "RTX_PRO_6000", 8)


def test_b200_8_on_2510_validated():
    assert is_validated_topology("25.10", "B200", 8)


def test_b200_8_on_2604_validated():
    assert is_validated_topology("26.04", "B200", 8)


def test_h200_on_2504_not_validated():
    # 25.04 is EOL and no longer a validated OS.
    assert not is_validated_topology("25.04", "H200", 8)


def test_b300_on_2604_not_validated():
    # B300 host setup works but is not yet validated end-to-end.
    assert not is_validated_topology("26.04", "B300", 8)


def test_wrong_gpu_count_not_validated():
    assert not is_validated_topology("25.10", "H200", 4)


def test_validated_rows_match_known_pairs():
    rows = validated_topology_rows()
    assert ("25.10", "H200", 8) in rows
    assert ("25.10", "B200", 8) in rows
    assert ("25.10", "RTX_PRO_6000", 8) in rows
    assert ("26.04", "H200", 8) in rows
    assert ("26.04", "B200", 8) in rows
    assert ("26.04", "RTX_PRO_6000", 8) in rows
    assert len(rows) == 6


def test_format_matrix_mentions_all_skus():
    text = format_topology_matrix()
    assert "25.10" in text
    assert "26.04" in text
    assert "H200" in text
    assert "RTX Pro 6000" in text
    assert "B200" in text


def test_format_matrix_h200_note_requires_nvswitch():
    text = format_topology_matrix()
    assert "NVSwitch" in text


def test_every_validated_row_has_operator_note():
    for row in validated_topology_rows():
        assert row in support_matrix._VALIDATED_NOTES, f"missing note for {row}"
