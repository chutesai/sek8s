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
from sek8s.services._shared import HOTKEY_HEADER, NONCE_HEADER, SIGNATURE_HEADER


@pytest.fixture
def helm_client(manager_app_no_auth):
    """Test client for helm endpoints (auth bypassed via manager_app_no_auth)."""
    with TestClient(manager_app_no_auth) as client:
        yield client


@pytest.fixture
def helm_client_with_auth(manager_app_with_auth):
    """Test client for helm endpoints with real auth (for 401/403 tests)."""
    with TestClient(manager_app_with_auth) as client:
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


def test_list_releases_401_without_auth(helm_client_with_auth):
    """GET /helm/releases returns 401 when no auth headers provided."""
    response = helm_client_with_auth.get("/helm/releases")
    assert response.status_code == 401


def test_list_releases_401_with_invalid_hotkey(helm_client_with_auth):
    """GET /helm/releases returns 401 when hotkey not in allowed signers."""
    import time

    response = helm_client_with_auth.get(
        "/helm/releases",
        headers={
            HOTKEY_HEADER: "5SomeUnknownHotkeyNotInAllowedList12345678901234567890",
            NONCE_HEADER: str(int(time.time())),
            SIGNATURE_HEADER: "00" * 32,
        },
    )
    assert response.status_code == 401


def test_start_upgrade_401_without_auth(helm_client_with_auth):
    """POST /helm/upgrade returns 401 when no auth headers provided."""
    response = helm_client_with_auth.post(
        "/helm/upgrade",
        json={"release": "chutes", "version": "0.2.1"},
    )
    assert response.status_code == 401


def test_get_release_status_401_without_auth(helm_client_with_auth):
    """GET /helm/releases/{name}/status returns 401 when no auth headers provided."""
    response = helm_client_with_auth.get("/helm/releases/chutes/status")
    assert response.status_code == 401


def test_get_upgrade_status_401_without_auth(helm_client_with_auth):
    """GET /helm/upgrade/status returns 401 when no auth headers provided."""
    response = helm_client_with_auth.get("/helm/upgrade/status")
    assert response.status_code == 401


@pytest.fixture
def helm_app_rate_limit_2():
    """App with auth bypassed and global rate limit 2/sec for rate limit testing."""
    import os
    import sys
    from unittest.mock import patch

    os.environ["HELM_RATE_LIMIT_PER_SECOND"] = "2"

    def _noop_authorize(*args, **kwargs):
        def _dep():
            return None

        return _dep

    try:
        with patch("sek8s.services.util.authorize", side_effect=_noop_authorize):
            for mod in list(sys.modules.keys()):
                if mod in (
                    "sek8s.services.manager",
                    "sek8s.system_manager.status.router",
                    "sek8s.system_manager.helm.router",
                ):
                    del sys.modules[mod]
            from sek8s.services.manager import create_app

            yield create_app()
    finally:
        os.environ.pop("HELM_RATE_LIMIT_PER_SECOND", None)


def test_helm_rate_limit_returns_429(
    helm_app_rate_limit_2, mock_create_subprocess_exec
):
    """Helm endpoints return 429 when rate limit exceeded."""
    mock_create_subprocess_exec.return_value = make_mock_process(
        0, HELM_LIST_ONE_RELEASE, ""
    )
    with TestClient(helm_app_rate_limit_2) as client:
        r1 = client.get("/helm/releases")
        r2 = client.get("/helm/releases")
        r3 = client.get("/helm/releases")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    assert "Too many requests" in str(r3.json().get("detail", ""))
