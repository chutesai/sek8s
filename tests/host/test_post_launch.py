"""Unit tests for post-launch host tuning helpers."""

from chutes.guest.post_launch import expand_cpulist


def test_expand_cpulist_single_range():
    assert expand_cpulist("0-3") == [1, 2, 3]


def test_expand_cpulist_multiple_ranges():
    assert expand_cpulist("0-1,4-5") == [1, 4, 5]


def test_expand_cpulist_excludes_cpu_zero():
    assert 0 not in expand_cpulist("0,2,4")
