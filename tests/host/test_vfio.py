"""Tests for VFIO PCI cleanup and binding helpers."""

from unittest.mock import mock_open, patch

from chutes.guest.vfio import _get_bound_driver, pci_cleanup_stale_devices


class _FakeLink:
    """Helper for faking os.path.islink / os.readlink via side_effect."""

    def __init__(self, links: dict[str, str]):
        self._links = links

    def islink(self, path):
        return path in self._links

    def realpath(self, path):
        return self._links.get(path, path)


# ---------------------------------------------------------------------------
# _get_bound_driver
# ---------------------------------------------------------------------------


@patch("os.path.realpath", return_value="/sys/bus/pci/drivers/vfio-pci")
@patch("os.path.islink", return_value=True)
def test_get_bound_driver_returns_driver_name(mock_islink, mock_realpath):
    assert _get_bound_driver("0000:b8:00.0") == "vfio-pci"
    mock_islink.assert_called_once_with("/sys/bus/pci/devices/0000:b8:00.0/driver")


@patch("os.path.islink", return_value=False)
def test_get_bound_driver_returns_none_when_unbound(mock_islink):
    assert _get_bound_driver("0000:b8:00.0") is None


# ---------------------------------------------------------------------------
# pci_cleanup_stale_devices
# ---------------------------------------------------------------------------


@patch("os.path.exists", return_value=True)
@patch("chutes.guest.vfio._get_bound_driver", return_value="nvidia")
def test_cleanup_noop_when_not_vfio(mock_driver, mock_exists):
    """Devices not on vfio-pci should not be removed."""
    with patch("builtins.open", mock_open()) as m:
        pci_cleanup_stale_devices(["0000:b8:00.0"])
    m.assert_not_called()


@patch("time.sleep")
@patch("time.monotonic")
@patch("os.path.exists")
@patch("chutes.guest.vfio._get_bound_driver", return_value="vfio-pci")
def test_cleanup_removes_and_rescans(
    mock_driver, mock_exists, mock_monotonic, mock_sleep
):
    """Devices on vfio-pci should be removed, then rescan triggers, then poll."""
    mock_monotonic.side_effect = [0.0, 0.1]
    mock_exists.return_value = True

    written = {}

    def fake_open(path, *args, **kwargs):
        m = mock_open()()
        written[path] = m
        return m

    with patch("builtins.open", side_effect=fake_open):
        pci_cleanup_stale_devices(["0000:b8:00.0"], timeout=5.0)

    assert "/sys/bus/pci/devices/0000:b8:00.0/remove" in written
    written["/sys/bus/pci/devices/0000:b8:00.0/remove"].write.assert_called_with("1")
    assert "/sys/bus/pci/rescan" in written
    written["/sys/bus/pci/rescan"].write.assert_called_with("1")


@patch("time.sleep")
@patch("time.monotonic")
@patch("os.path.exists", return_value=True)
@patch("chutes.guest.vfio._get_bound_driver")
def test_cleanup_handles_multiple_devices(
    mock_driver, mock_exists, mock_monotonic, mock_sleep
):
    """Multiple vfio-pci devices should all be removed."""
    mock_driver.side_effect = ["vfio-pci", "vfio-pci", None]
    mock_monotonic.side_effect = [0.0, 0.1]

    written_paths = []

    def fake_open(path, *args, **kwargs):
        written_paths.append(path)
        return mock_open()()

    devices = ["0000:b8:00.0", "0000:b9:00.0", "0000:ba:00.0"]
    with patch("builtins.open", side_effect=fake_open):
        pci_cleanup_stale_devices(devices, timeout=5.0)

    assert "/sys/bus/pci/devices/0000:b8:00.0/remove" in written_paths
    assert "/sys/bus/pci/devices/0000:b9:00.0/remove" in written_paths
    assert "/sys/bus/pci/devices/0000:ba:00.0/remove" not in written_paths
    assert "/sys/bus/pci/rescan" in written_paths


@patch("time.sleep")
@patch("time.monotonic")
@patch("os.path.exists")
@patch("chutes.guest.vfio._get_bound_driver", return_value="vfio-pci")
def test_cleanup_warns_on_remove_failure(
    mock_driver, mock_exists, mock_monotonic, mock_sleep, capsys
):
    """OSError during remove should warn, not raise."""
    mock_exists.return_value = True
    mock_monotonic.side_effect = [0.0, 0.1]

    call_count = 0

    def fake_open(path, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if "remove" in path:
            raise OSError("permission denied")
        return mock_open()()

    with patch("builtins.open", side_effect=fake_open):
        pci_cleanup_stale_devices(["0000:b8:00.0"])

    captured = capsys.readouterr()
    assert "Warning: could not remove 0000:b8:00.0" in captured.out
