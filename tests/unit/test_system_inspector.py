import pytest
from fastapi.testclient import TestClient

from sek8s.config import SystemInspectorConfig
from sek8s.services.system_inspector import (
    CommandResult,
    SystemInspectorServer,
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
    monkeypatch.setattr("sek8s.services.system_inspector._run_command", runner)
    return runner


@pytest.fixture
def inspector_client():
    config = SystemInspectorConfig(uds_path="/tmp/system-inspector.sock")
    server = SystemInspectorServer(config)
    with TestClient(server.app) as client:
        yield client


def test_list_services(inspector_client):
    response = inspector_client.get("/services")
    assert response.status_code == 200
    data = response.json()
    service_ids = {svc["id"] for svc in data["services"]}
    assert {"admission-controller", "attestation-service", "k3s"}.issubset(service_ids)


def test_service_status_parsing(inspector_client, fake_runner):
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

    response = inspector_client.get("/services/admission-controller/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"]["active_state"] == "active"
    assert data["status"]["main_pid"] == "1234"
    assert fake_runner.commands[-1][0] == "systemctl"


def test_logs_endpoint_respects_clamp(inspector_client, fake_runner):
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

    response = inspector_client.get("/services/k3s/logs?lines=5001")
    assert response.status_code == 200
    data = response.json()
    assert data["returned_lines"] == 2
    # Ensure journalctl was invoked with clamped line count
    assert any("--lines=1000" in arg for arg in fake_runner.commands[-1])


def test_nvidia_smi_command_building(inspector_client, fake_runner):
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

    response = inspector_client.get("/gpu/nvidia-smi?detail=true&gpu=0")
    assert response.status_code == 200
    data = response.json()
    assert data["command"] == ["nvidia-smi", "-q", "-i", "0"]
    assert fake_runner.commands[-1] == ["nvidia-smi", "-q", "-i", "0"]


def test_unknown_service_returns_404(inspector_client):
    response = inspector_client.get("/services/unknown/status")
    assert response.status_code == 404