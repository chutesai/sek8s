"""Unit tests for HelmManager."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from fixtures.helm import HELM_LIST_EMPTY, HELM_LIST_ONE_RELEASE, HELM_STATUS_DEPLOYED
from fixtures.process import make_mock_process
from sek8s.system_manager.helm.models import UpgradeStatusEnum


@pytest.mark.asyncio
async def test_list_releases_success(helm_manager, mock_create_subprocess_exec):
    """list_releases parses helm list JSON and returns ReleaseEntry list."""
    mock_create_subprocess_exec.return_value = make_mock_process(
        0, HELM_LIST_ONE_RELEASE, ""
    )
    releases = await helm_manager.list_releases()

    assert len(releases) == 1
    assert releases[0].name == "chutes"
    assert releases[0].namespace == "chutes"
    assert releases[0].chart == "chutes-miner-gpu-0.2.1"
    assert releases[0].status == "deployed"
    assert releases[0].revision == 3
    assert releases[0].updated == "2026-03-20T12:00:00Z"
    assert releases[0].app_version == "1.0.0"


@pytest.mark.asyncio
async def test_list_releases_failure(helm_manager, mock_create_subprocess_exec):
    """list_releases raises HTTPException when helper fails."""
    mock_create_subprocess_exec.return_value = make_mock_process(
        1, "", "helm list failed"
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await helm_manager.list_releases()

    assert exc_info.value.status_code == 502
    assert "helm_list_failed" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_get_release_status_success(helm_manager, mock_create_subprocess_exec):
    """get_release_status returns parsed helm status JSON."""
    mock_create_subprocess_exec.return_value = make_mock_process(
        0, HELM_STATUS_DEPLOYED, ""
    )
    result = await helm_manager.get_release_status("chutes")

    assert result["status"] == "deployed"
    assert result["chart"] == "chutes-miner-gpu-0.2.1"
    assert result["app_version"] == "1.0.0"


@pytest.mark.asyncio
async def test_start_upgrade_returns_started(helm_manager, mock_create_subprocess_exec):
    """start_upgrade returns started and spawns background task."""
    mock_create_subprocess_exec.side_effect = [
        make_mock_process(0, HELM_LIST_EMPTY, ""),
        make_mock_process(0, "", ""),
    ]
    status, up_to_date = await helm_manager.start_upgrade("chutes", "0.2.1")

    assert status == "started"
    assert up_to_date is False
    assert helm_manager.get_upgrade_status() is not None
    assert helm_manager.get_upgrade_status().status == UpgradeStatusEnum.IN_PROGRESS

    for _ in range(100):
        snapshot = helm_manager.get_upgrade_status()
        if snapshot is None or snapshot.status != UpgradeStatusEnum.IN_PROGRESS:
            break
        await asyncio.sleep(0)
    snapshot = helm_manager.get_upgrade_status()
    assert snapshot is not None, "Expected upgrade snapshot"
    assert snapshot.status == UpgradeStatusEnum.COMPLETED, (
        f"Expected COMPLETED, got {snapshot.status}: {snapshot.error}"
    )
    assert snapshot.error is None


@pytest.mark.asyncio
async def test_start_upgrade_returns_in_progress_when_running(
    helm_manager, mock_create_subprocess_exec
):
    """start_upgrade returns in_progress when upgrade already in progress."""
    mock_create_subprocess_exec.return_value = make_mock_process(
        0, HELM_LIST_EMPTY, ""
    )
    blocker = asyncio.Future()
    real_create_task = asyncio.create_task

    async def never_complete():
        await blocker

    def fake_create_task(coro):
        coro.close()
        return real_create_task(never_complete())

    with patch(
        "sek8s.system_manager.helm.manager.asyncio.create_task",
        side_effect=fake_create_task,
    ):
        await helm_manager.start_upgrade("chutes")
        await asyncio.sleep(0)
        status, _ = await helm_manager.start_upgrade("chutes", "0.2.1")
        assert status == "in_progress"
    blocker.set_result(None)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_start_upgrade_returns_up_to_date_when_version_matches(
    helm_manager, mock_create_subprocess_exec
):
    """start_upgrade returns up_to_date when release already at requested version."""
    mock_create_subprocess_exec.return_value = make_mock_process(
        0, HELM_LIST_ONE_RELEASE, ""
    )
    status, up_to_date = await helm_manager.start_upgrade("chutes", "0.2.1")

    assert status == "up_to_date"
    assert up_to_date is True
    assert helm_manager.get_upgrade_status() is None


@pytest.mark.asyncio
async def test_get_upgrade_status_none_when_idle(helm_manager):
    """get_upgrade_status returns None when no upgrade in progress."""
    assert helm_manager.get_upgrade_status() is None


@pytest.mark.asyncio
async def test_upgrade_failure_records_error(
    helm_manager, mock_create_subprocess_exec
):
    """Failed upgrade records error in get_upgrade_status."""
    mock_create_subprocess_exec.side_effect = [
        make_mock_process(0, HELM_LIST_EMPTY, ""),
        make_mock_process(1, "", "version not found"),
    ]
    await helm_manager.start_upgrade("chutes", "99.99.99")
    for _ in range(100):
        snapshot = helm_manager.get_upgrade_status()
        if snapshot is None or snapshot.status != UpgradeStatusEnum.IN_PROGRESS:
            break
        await asyncio.sleep(0)
    snapshot = helm_manager.get_upgrade_status()
    assert snapshot is not None
    assert snapshot.status == UpgradeStatusEnum.FAILED
    assert "version not found" in (snapshot.error or "")
