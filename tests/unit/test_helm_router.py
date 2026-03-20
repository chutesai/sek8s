"""Unit tests for helm router endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fixtures.helm import (
    HELM_LIST_EMPTY,
    HELM_LIST_ONE_RELEASE,
    HELM_STATUS_DEPLOYED,
)
from fixtures.process import make_mock_process


@pytest.fixture
def helm_client(manager_app_no_auth):
    """Test client for helm endpoints (auth bypassed via manager_app_no_auth)."""
    with TestClient(manager_app_no_auth) as client:
        yield client


def test_list_releases_success(helm_client, mock_create_subprocess_exec):
    """GET /helm/releases returns 200 with release list."""
    mock_create_subprocess_exec.return_value = make_mock_process(
        0, HELM_LIST_ONE_RELEASE, ""
    )
    response = helm_client.get("/helm/releases")
    assert response.status_code == 200
    data = response.json()
    assert "releases" in data
    assert len(data["releases"]) == 1
    assert data["releases"][0]["name"] == "chutes"
    assert data["releases"][0]["chart"] == "chutes-miner-gpu-0.2.1"


def test_list_releases_502_on_helper_failure(helm_client, mock_create_subprocess_exec):
    """GET /helm/releases returns 502 when k3s-helm-helper fails."""
    mock_create_subprocess_exec.return_value = make_mock_process(
        1, "", "helm list failed"
    )
    response = helm_client.get("/helm/releases")
    assert response.status_code == 502
    data = response.json()
    assert "detail" in data
    assert "helm_list_failed" in str(data["detail"])


def test_get_release_status_success(helm_client, mock_create_subprocess_exec):
    """GET /helm/releases/{name}/status returns 200 with status details."""
    mock_create_subprocess_exec.return_value = make_mock_process(
        0, HELM_STATUS_DEPLOYED, ""
    )
    response = helm_client.get("/helm/releases/chutes/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "deployed"
    assert data["chart"] == "chutes-miner-gpu-0.2.1"
    assert "info" in data


def test_get_release_status_502_on_helper_failure(
    helm_client, mock_create_subprocess_exec
):
    """GET /helm/releases/{name}/status returns 502 when k3s-helm-helper fails."""
    mock_create_subprocess_exec.return_value = make_mock_process(
        1, "", "helm status failed"
    )
    response = helm_client.get("/helm/releases/chutes/status")
    assert response.status_code == 502
    data = response.json()
    assert "helm_status_failed" in str(data["detail"])


def test_start_upgrade_returns_started(helm_client, mock_create_subprocess_exec):
    """POST /helm/upgrade returns 200 with status started."""
    mock_create_subprocess_exec.side_effect = [
        make_mock_process(0, HELM_LIST_EMPTY, ""),
        make_mock_process(0, "", ""),
    ]
    response = helm_client.post(
        "/helm/upgrade",
        json={"release": "chutes", "version": "0.2.1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["release"] == "chutes"
    assert data["status"] == "started"


def test_start_upgrade_returns_up_to_date(helm_client, mock_create_subprocess_exec):
    """POST /helm/upgrade returns 200 with status up_to_date when already at version."""
    mock_create_subprocess_exec.return_value = make_mock_process(
        0, HELM_LIST_ONE_RELEASE, ""
    )
    response = helm_client.post(
        "/helm/upgrade",
        json={"release": "chutes", "version": "0.2.1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["release"] == "chutes"
    assert data["status"] == "up_to_date"


def test_upgrade_status_200_when_in_progress(helm_client, mock_create_subprocess_exec):
    """GET /helm/upgrade/status returns 200 when upgrade in progress."""
    mock_create_subprocess_exec.side_effect = [
        make_mock_process(0, HELM_LIST_EMPTY, ""),
        make_mock_process(0, "", ""),
    ]
    helm_client.post("/helm/upgrade", json={"release": "chutes"})
    response = helm_client.get("/helm/upgrade/status")
    assert response.status_code == 200
    data = response.json()
    assert data["release"] == "chutes"
    assert data["status"] in ("in_progress", "completed")


def test_upgrade_status_404_when_no_upgrade(helm_client):
    """GET /helm/upgrade/status returns 404 when no upgrade in progress."""
    response = helm_client.get("/helm/upgrade/status")
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert "No upgrade" in str(data["detail"])
