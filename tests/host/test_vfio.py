"""Tests for VFIO PCI cleanup and binding helpers."""

import subprocess
from unittest.mock import MagicMock, patch

from chutes.guest.vfio import (
    _get_bound_driver,
    has_stale_vfio_devices,
    unbind_stale_vfio_devices,
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
# unbind_stale_vfio_devices
# ---------------------------------------------------------------------------


@patch("chutes.guest.vfio._sysfs_write")
@patch("chutes.guest.vfio._get_bound_driver", return_value="nvidia")
def test_unbind_noop_when_not_vfio(mock_driver, mock_write):
    """Devices not on vfio-pci should not be unbound."""
    unbind_stale_vfio_devices(["0000:b8:00.0"])
    mock_write.assert_not_called()


@patch("chutes.guest.vfio._sysfs_write", return_value=True)
@patch("chutes.guest.vfio._get_bound_driver", return_value="vfio-pci")
def test_unbind_writes_unbind_and_clears_override(mock_driver, mock_write):
    """Devices on vfio-pci should be unbound and have driver_override cleared."""
    unbind_stale_vfio_devices(["0000:b8:00.0"])

    calls = [(c[0][0], c[0][1]) for c in mock_write.call_args_list]
    assert ("/sys/bus/pci/drivers/vfio-pci/unbind", "0000:b8:00.0") in calls
    assert ("/sys/bus/pci/devices/0000:b8:00.0/driver_override", "") in calls


@patch("chutes.guest.vfio._sysfs_write", return_value=True)
@patch("chutes.guest.vfio._get_bound_driver")
def test_unbind_handles_multiple_devices(mock_driver, mock_write):
    """Multiple vfio-pci devices should all be unbound; non-vfio skipped."""
    mock_driver.side_effect = ["vfio-pci", "vfio-pci", None]

    devices = ["0000:b8:00.0", "0000:b9:00.0", "0000:ba:00.0"]
    unbind_stale_vfio_devices(devices)

    written = [(c[0][0], c[0][1]) for c in mock_write.call_args_list]
    assert ("/sys/bus/pci/drivers/vfio-pci/unbind", "0000:b8:00.0") in written
    assert ("/sys/bus/pci/drivers/vfio-pci/unbind", "0000:b9:00.0") in written
    assert ("/sys/bus/pci/devices/0000:b8:00.0/driver_override", "") in written
    assert ("/sys/bus/pci/devices/0000:b9:00.0/driver_override", "") in written
    assert all("0000:ba:00.0" not in str(c) for c in written)


@patch("chutes.guest.vfio._sysfs_write", return_value=False)
@patch("chutes.guest.vfio._get_bound_driver", return_value="vfio-pci")
def test_unbind_warns_on_timeout(mock_driver, mock_write, capsys):
    """Timed-out unbind should warn, not raise, and not clear override."""
    unbind_stale_vfio_devices(["0000:b8:00.0"])

    captured = capsys.readouterr()
    assert "timed out" in captured.out
    assert mock_write.call_count == 1
