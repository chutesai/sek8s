import importlib

import pytest
from fastapi.testclient import TestClient

from sek8s.system_manager.status.models import (
    CommandResult,
    SERVICE_ALLOWLIST,
)


class FakeRunner:
    def __init__(self):
        self.commands = []
        self.responses: dict[str, CommandResult] = {}

    def set_response(self, binary: str, result: CommandResult) -> None:
        self.responses[binary] = result

    async def __call__(self, command, timeout, limit):  # pragma: no cover - interface shim
        self.commands.append(command)
        binary = command[0]
        if binary not in self.responses:
            raise AssertionError(f"No response registered for {binary}")
        return self.responses[binary]


@pytest.fixture
def fake_runner(monkeypatch):
    runner = FakeRunner()
    util_mod = importlib.import_module("sek8s.system_manager.status.util")
    router_mod = importlib.import_module("sek8s.system_manager.status.router")
    monkeypatch.setattr(util_mod, "run_command", runner)
    monkeypatch.setattr(router_mod, "run_command", runner)
    return runner


@pytest.fixture
def status_client(manager_app_no_auth):
    """Test client for status endpoints (auth bypassed via manager_app_no_auth)."""
    with TestClient(manager_app_no_auth) as client:
        yield client


def test_list_services(status_client):
    response = status_client.get("/status/services")
    assert response.status_code == 200
    data = response.json()
    service_ids = {svc["id"] for svc in data["services"]}
    expected = {
        "admission-controller",
        "attestation-service",
        "k3s",
        "nvidia-persistenced",
        "nvidia-fabricmanager",
        "system-manager",
    }
    assert expected.issubset(service_ids)


def test_service_status_parsing(status_client, fake_runner):
    fake_runner.set_response(
        "systemctl",
        CommandResult(
            exit_code=0,
            stdout=(
                "Id=admission-controller.service\n"
                "LoadState=loaded\n"
                "ActiveState=active\n"
                "SubState=running\n"
                "MainPID=1234\n"
                "ExecMainStatus=0\n"
                "ExecMainCode=0\n"
                "UnitFileState=enabled\n"
            ),
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/services/admission-controller/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"]["active_state"] == "active"
    assert data["status"]["main_pid"] == "1234"
    assert data["healthy"] is True
    assert fake_runner.commands[-1][0] == "systemctl"


def test_logs_endpoint_respects_clamp(status_client, fake_runner):
    fake_runner.set_response(
        "journalctl",
        CommandResult(
            exit_code=0,
            stdout="line1\nline2\n",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/services/k3s/logs?lines=5001")
    assert response.status_code == 200
    data = response.json()
    assert data["returned_lines"] == 2
    assert any("--lines=1000" in arg for arg in fake_runner.commands[-1])


def test_nvidia_smi_command_building(status_client, fake_runner):
    fake_runner.set_response(
        "nvidia-smi",
        CommandResult(
            exit_code=0,
            stdout="gpu output\nsecond line",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/gpu/nvidia-smi?detail=true&gpu=0")
    assert response.status_code == 200
    data = response.json()
    assert data["command"] == ["nvidia-smi", "-q", "-i", "0"]
    assert fake_runner.commands[-1] == ["nvidia-smi", "-q", "-i", "0"]
    assert data["stdout_lines"] == ["gpu output", "second line"]


def test_unknown_service_returns_404(status_client):
    response = status_client.get("/status/services/unknown/status")
    assert response.status_code == 404


def test_overview_success(status_client, fake_runner):
    fake_runner.set_response(
        "systemctl",
        CommandResult(
            exit_code=0,
            stdout=(
                "Id=admission-controller.service\n"
                "LoadState=loaded\n"
                "ActiveState=active\n"
                "SubState=running\n"
                "MainPID=1234\n"
                "ExecMainStatus=0\n"
                "ExecMainCode=0\n"
                "UnitFileState=enabled\n"
            ),
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )
    fake_runner.set_response(
        "nvidia-smi",
        CommandResult(
            exit_code=0,
            stdout="gpu output",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert len(data["services"]) == len(SERVICE_ALLOWLIST)
    assert all(entry["healthy"] for entry in data["services"])
    assert data["gpu"]["status"] == "ok"


def test_overview_degraded_on_service_failure(status_client, fake_runner):
    fake_runner.set_response(
        "systemctl",
        CommandResult(
            exit_code=2,
            stdout="",
            stderr="boom",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )
    fake_runner.set_response(
        "nvidia-smi",
        CommandResult(
            exit_code=0,
            stdout="gpu output",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert any(entry.get("error") for entry in data["services"])
