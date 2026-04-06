"""Tests for VFIO PCI cleanup and binding helpers."""

import subprocess
from unittest.mock import MagicMock, patch

from chutes.guest.vfio import (
    _get_bound_driver,
    has_stale_vfio_devices,
    pci_cleanup_stale_devices,
)


# ---------------------------------------------------------------------------
# _get_bound_driver
# ---------------------------------------------------------------------------


@patch("os.path.realpath", return_value="/sys/bus/pci/drivers/vfio-pci")
@patch("os.path.islink", return_value=True)
def test_get_bound_driver_returns_driver_name(mock_islink, mock_realpath):
    assert _get_bound_driver("0000:b8:00.0") == "vfio-pci"
    mock_islink.assert_called_once_with(
        "/sys/bus/pci/devices/0000:b8:00.0/driver"
    )


@patch("os.path.islink", return_value=False)
def test_get_bound_driver_returns_none_when_unbound(mock_islink):
    assert _get_bound_driver("0000:b8:00.0") is None


# ---------------------------------------------------------------------------
# has_stale_vfio_devices
# ---------------------------------------------------------------------------


@patch("chutes.guest.vfio._get_bound_driver")
def test_has_stale_vfio_devices_true(mock_driver):
    mock_driver.side_effect = [None, "vfio-pci", None]
    assert has_stale_vfio_devices(["0000:a:00.0", "0000:b:00.0", "0000:c:00.0"])


@patch("chutes.guest.vfio._get_bound_driver", return_value=None)
def test_has_stale_vfio_devices_false(mock_driver):
    assert not has_stale_vfio_devices(["0000:a:00.0", "0000:b:00.0"])


# ---------------------------------------------------------------------------
# pci_cleanup_stale_devices
# ---------------------------------------------------------------------------


@patch("chutes.guest.vfio._sysfs_write")
@patch("chutes.guest.vfio._get_bound_driver", return_value="nvidia")
def test_cleanup_noop_when_not_vfio(mock_driver, mock_write):
    """Devices not on vfio-pci should not be removed."""
    pci_cleanup_stale_devices(["0000:b8:00.0"])
    mock_write.assert_not_called()


@patch("time.sleep")
@patch("time.monotonic")
@patch("os.path.exists", return_value=True)
@patch("chutes.guest.vfio._sysfs_write", return_value=True)
@patch("chutes.guest.vfio._get_bound_driver", return_value="vfio-pci")
def test_cleanup_removes_and_rescans(
    mock_driver, mock_write, mock_exists, mock_monotonic, mock_sleep
):
    """Devices on vfio-pci should be removed via sysfs, then rescan triggers."""
    mock_monotonic.side_effect = [0.0, 0.1]

    pci_cleanup_stale_devices(["0000:b8:00.0"], timeout=5.0)

    calls = [c[0] for c in mock_write.call_args_list]
    assert ("/sys/bus/pci/devices/0000:b8:00.0/remove", "1") == calls[0][:2]
    assert ("/sys/bus/pci/rescan", "1") == calls[1][:2]


@patch("time.sleep")
@patch("time.monotonic")
@patch("os.path.exists", return_value=True)
@patch("chutes.guest.vfio._sysfs_write", return_value=True)
@patch("chutes.guest.vfio._get_bound_driver")
def test_cleanup_handles_multiple_devices(
    mock_driver, mock_write, mock_exists, mock_monotonic, mock_sleep
):
    """Multiple vfio-pci devices should all be removed; non-vfio skipped."""
    mock_driver.side_effect = ["vfio-pci", "vfio-pci", None]
    mock_monotonic.side_effect = [0.0, 0.1]

    devices = ["0000:b8:00.0", "0000:b9:00.0", "0000:ba:00.0"]
    pci_cleanup_stale_devices(devices, timeout=5.0)

    written_paths = [c[0][0] for c in mock_write.call_args_list]
    assert "/sys/bus/pci/devices/0000:b8:00.0/remove" in written_paths
    assert "/sys/bus/pci/devices/0000:b9:00.0/remove" in written_paths
    assert "/sys/bus/pci/devices/0000:ba:00.0/remove" not in written_paths
    assert "/sys/bus/pci/rescan" in written_paths


@patch("chutes.guest.vfio._sysfs_write", return_value=False)
@patch("chutes.guest.vfio._get_bound_driver", return_value="vfio-pci")
def test_cleanup_warns_on_remove_timeout(mock_driver, mock_write, capsys):
    """Timed-out remove should warn, not raise, and not attempt rescan."""
    pci_cleanup_stale_devices(["0000:b8:00.0"])

    captured = capsys.readouterr()
    assert "timed out" in captured.out
    assert mock_write.call_count == 1
