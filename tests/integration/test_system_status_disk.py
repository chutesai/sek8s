"""
Integration tests for system status disk space endpoints.

Tests both simple and diagnostic modes with real filesystem interaction.
Uses the manager app (system_status router).

NOTE: These tests require sudo access to run the 'du' command. In CI/CD environments,
run these tests in a Docker container with appropriate sudoers configuration, or
configure the test environment to allow passwordless sudo for the 'du' command:
    echo "$USER ALL=(ALL) NOPASSWD: /usr/bin/du" | sudo tee /etc/sudoers.d/test-du

Alternatively, skip these tests in environments without sudo by running:
    pytest -m "not requires_sudo"
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# Check if sudo is available for du command
def _can_use_sudo_du():
    """Check if current user can run sudo du without password."""
    try:
        result = subprocess.run(
            ["sudo", "-n", "du", "--version"],
            capture_output=True,
            timeout=2
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# Skip tests if sudo is not available
pytestmark = pytest.mark.skipif(
    not _can_use_sudo_du(),
    reason="sudo access required for du command. Run in Docker or configure sudoers."
)


@pytest.fixture
def test_dir_structure():
    """
    Create a temporary directory structure with known sizes for testing.

    Structure:
    test_root/
        level1_large/          (1MB)
            level2_medium/     (500KB)
                level3_small/  (100KB)
            level2_small/      (100KB)
        level1_medium/         (500KB)
            level2_large/      (400KB)
        level1_small/          (100KB)
    """
    temp_root = tempfile.mkdtemp(prefix="disk_test_")

    try:
        # Helper to create a file of specific size
        def create_file(path: Path, size_kb: int):
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'wb') as f:
                f.write(b'0' * (size_kb * 1024))

        root = Path(temp_root)

        # Level 1: Large directory (1MB total)
        l1_large = root / "level1_large"
        create_file(l1_large / "file1.dat", 400)  # 400KB in root

        # Level 2: Medium subdirectory (500KB)
        l2_medium = l1_large / "level2_medium"
        create_file(l2_medium / "file2.dat", 400)  # 400KB

        # Level 3: Small subdirectory (100KB)
        l3_small = l2_medium / "level3_small"
        create_file(l3_small / "file3.dat", 100)  # 100KB

        # Level 2: Small subdirectory (100KB)
        l2_small = l1_large / "level2_small"
        create_file(l2_small / "file4.dat", 100)  # 100KB

        # Level 1: Medium directory (500KB total)
        l1_medium = root / "level1_medium"
        create_file(l1_medium / "file5.dat", 100)  # 100KB in root

        # Level 2: Large subdirectory (400KB)
        l2_large = l1_medium / "level2_large"
        create_file(l2_large / "file6.dat", 400)  # 400KB

        # Level 1: Small directory (100KB)
        l1_small = root / "level1_small"
        create_file(l1_small / "file7.dat", 100)  # 100KB

        yield str(root)
    finally:
        # Cleanup
        shutil.rmtree(temp_root, ignore_errors=True)


@pytest.fixture
def status_client(manager_app_no_auth):
    """Test client for the manager app (status router; auth bypassed for tests)."""
    with TestClient(manager_app_no_auth) as client:
        yield client


@pytest.mark.asyncio
async def test_disk_space_simple_mode(status_client, test_dir_structure):
    """Test simple mode returns only immediate subdirectories."""
    response = status_client.get(f"/status/disk/space?path={test_dir_structure}")
    assert response.status_code == 200
    result = response.json()

    # Should have 3 level-1 directories
    assert len(result["directories"]) == 3
    assert result["diagnostic_mode"] is False
    assert result.get("max_depth") is None
    assert result.get("top_n") is None

    # All directories should be at depth 1
    assert all(d["depth"] == 1 for d in result["directories"])

    # Check directory names
    dir_names = {d["name"] for d in result["directories"]}
    assert dir_names == {"level1_large", "level1_medium", "level1_small"}

    # Verify ordering (should be sorted by size descending)
    sizes = [d["size_bytes"] for d in result["directories"]]
    assert sizes == sorted(sizes, reverse=True)

    # Check that level1_large is the largest
    assert result["directories"][0]["name"] == "level1_large"

    # Verify percentage calculations
    for d in result["directories"]:
        assert d.get("percentage") is not None
        assert 0 <= d["percentage"] <= 100

    # Total should be sum of all directories
    total_from_dirs = sum(d["size_bytes"] for d in result["directories"])
    assert result["total_size_bytes"] >= total_from_dirs


@pytest.mark.asyncio
async def test_disk_space_diagnostic_mode_depth_2(status_client, test_dir_structure):
    """Test diagnostic mode with depth 2."""
    response = status_client.get(
        f"/status/disk/space?path={test_dir_structure}&diagnostic=true&max_depth=2&top_n=10"
    )
    assert response.status_code == 200
    result = response.json()

    assert result["diagnostic_mode"] is True
    assert result["max_depth"] == 2
    assert result["top_n"] == 10

    # Should have directories from depth 1 and 2
    depths = {d["depth"] for d in result["directories"]}
    assert depths.issubset({1, 2})

    # Check we have some level 2 directories
    level2_dirs = [d for d in result["directories"] if d["depth"] == 2]
    assert len(level2_dirs) > 0

    # Verify level2_medium and level2_large are present
    level2_names = {d["name"] for d in level2_dirs}
    assert "level2_medium" in level2_names
    assert "level2_large" in level2_names

    # All directories should have percentages
    for d in result["directories"]:
        assert d.get("percentage") is not None
        assert 0 <= d["percentage"] <= 100

    # Verify full paths are included
    for d in result["directories"]:
        assert d["path"].startswith(test_dir_structure)


@pytest.mark.asyncio
async def test_disk_space_diagnostic_mode_depth_3(status_client, test_dir_structure):
    """Test diagnostic mode with depth 3 to reach deepest level."""
    response = status_client.get(
        f"/status/disk/space?path={test_dir_structure}&diagnostic=true&max_depth=3&top_n=10"
    )
    assert response.status_code == 200
    result = response.json()

    assert result["diagnostic_mode"] is True
    assert result["max_depth"] == 3

    # Should have directories from depth 1, 2, and 3
    depths = {d["depth"] for d in result["directories"]}
    assert depths.issubset({1, 2, 3})

    # Check we have the deepest directory
    level3_dirs = [d for d in result["directories"] if d["depth"] == 3]
    assert len(level3_dirs) > 0

    # Verify level3_small is present
    level3_names = {d["name"] for d in level3_dirs}
    assert "level3_small" in level3_names

    # Verify sorting by size (descending)
    sizes = [d["size_bytes"] for d in result["directories"]]
    assert sizes == sorted(sizes, reverse=True)


@pytest.mark.asyncio
async def test_disk_space_diagnostic_mode_top_n_limit(status_client, test_dir_structure):
    """Test that top_n limits results per level."""
    response = status_client.get(
        f"/status/disk/space?path={test_dir_structure}&diagnostic=true&max_depth=2&top_n=2"
    )
    assert response.status_code == 200
    result = response.json()

    # Count directories at each depth
    depth1_count = len([d for d in result["directories"] if d["depth"] == 1])
    depth2_count = len([d for d in result["directories"] if d["depth"] == 2])

    # We have 3 level1 dirs, but only top 2 should be returned
    assert depth1_count <= 2

    # We have 3 level2 dirs across all level1 dirs, but only top 2 should be returned
    assert depth2_count <= 2

    # Verify we got the largest ones
    if depth1_count > 0:
        level1_dirs = [d for d in result["directories"] if d["depth"] == 1]
        assert level1_dirs[0]["name"] == "level1_large"  # Should be the largest


@pytest.mark.asyncio
async def test_disk_space_diagnostic_mode_top_n_ordering(status_client, test_dir_structure):
    """Test that results are properly ordered by size across all depths."""
    response = status_client.get(
        f"/status/disk/space?path={test_dir_structure}&diagnostic=true&max_depth=3&top_n=10"
    )
    assert response.status_code == 200
    result = response.json()

    # Overall results should be sorted by size descending
    sizes = [d["size_bytes"] for d in result["directories"]]
    assert sizes == sorted(sizes, reverse=True)

    # The largest directory overall should be first
    assert result["directories"][0]["size_bytes"] >= result["directories"][-1]["size_bytes"]


@pytest.mark.asyncio
async def test_disk_space_invalid_path(status_client):
    """Test that invalid paths are rejected."""
    response = status_client.get("/status/disk/space?path=/nonexistent/path/that/does/not/exist")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_disk_space_file_not_directory(status_client, test_dir_structure):
    """Test that files are rejected (must be directories)."""
    # Create a file to test
    test_file = Path(test_dir_structure) / "test_file.txt"
    test_file.write_text("test content")

    response = status_client.get(f"/status/disk/space?path={test_file}")
    assert response.status_code == 400
    assert "not a directory" in str(response.json().get("detail", "")).lower()


@pytest.mark.asyncio
async def test_disk_space_percentage_calculations(status_client, test_dir_structure):
    """Test that percentage calculations are correct."""
    response = status_client.get(
        f"/status/disk/space?path={test_dir_structure}&diagnostic=true&max_depth=3&top_n=10"
    )
    assert response.status_code == 200
    result = response.json()

    # Each directory should have a percentage
    for d in result["directories"]:
        assert d.get("percentage") is not None
        # Percentage should be reasonable (0-100%)
        assert 0 <= d["percentage"] <= 100

        # Verify percentage calculation
        expected_percentage = (d["size_bytes"] / result["total_size_bytes"]) * 100
        # Allow small floating point differences
        assert abs(d["percentage"] - expected_percentage) < 0.01


@pytest.mark.asyncio
async def test_disk_space_human_readable_format(status_client, test_dir_structure):
    """Test that human-readable sizes are properly formatted."""
    response = status_client.get(f"/status/disk/space?path={test_dir_structure}")
    assert response.status_code == 200
    result = response.json()

    for d in result["directories"]:
        assert d.get("size_human") is not None
        # Should contain a unit
        assert any(unit in d["size_human"] for unit in ["B", "KB", "MB", "GB", "TB"])
        # Should contain a number
        assert any(char.isdigit() for char in d["size_human"])

    # Total should also have human-readable format
    assert result.get("total_size_human") is not None
    assert any(unit in result["total_size_human"] for unit in ["B", "KB", "MB", "GB", "TB"])


@pytest.mark.asyncio
async def test_disk_space_empty_directory(status_client):
    """Test behavior with an empty directory."""
    temp_dir = tempfile.mkdtemp(prefix="empty_test_")

    try:
        response = status_client.get(f"/status/disk/space?path={temp_dir}")
        assert response.status_code == 200
        result = response.json()

        # Should have no subdirectories
        assert len(result["directories"]) == 0

        # Total size should be minimal (just directory overhead)
        assert result["total_size_bytes"] >= 0

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
