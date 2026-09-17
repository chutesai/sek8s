import importlib

import pytest
from fastapi.testclient import TestClient

from sek8s.system_manager.status.models import SERVICE_ALLOWLIST, CommandResult


class FakeProcess:
    """Stands in for the object asyncio.create_subprocess_exec returns."""

    def __init__(self, result: CommandResult):
        self._result = result
        self.returncode = result.exit_code

    async def communicate(self):
        return self._result.stdout.encode(), self._result.stderr.encode()


class FakeRunner:
    """Fakes the SUBPROCESS, not run_command.

    It used to replace `run_command` wholesale, which meant the function under test never
    ran: the shim had to re-implement the `check` contract itself, so every test asserting
    "a failed command produces a 500" was asserting the shim. Deleting the raise from
    util.run_command left all of them green -- verified by mutation.

    Faking one layer down runs the real `run_command`: its exit-code check, output
    truncation, timeout handling and error detail are all exercised, and only the process
    is synthetic.
    """

    def __init__(self):
        self.commands = []
        self.responses: dict[str, CommandResult] = {}

    def set_response(self, binary: str, result: CommandResult) -> None:
        self.responses[binary] = result

    async def __call__(self, *command, **_kwargs):
        self.commands.append(list(command))
        binary = command[0]
        if binary not in self.responses:
            raise AssertionError(f"No response registered for {binary}")
        return FakeProcess(self.responses[binary])


@pytest.fixture
def fake_runner(monkeypatch):
    runner = FakeRunner()
    util_mod = importlib.import_module("sek8s.system_manager.status.util")
    monkeypatch.setattr(util_mod.asyncio, "create_subprocess_exec", runner)
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
        "chute-log-shipper",
        "k3s",
        "nvidia-persistenced",
        "nvidia-fabricmanager",
        "opa",
        "system-manager",
    }
    assert expected.issubset(service_ids)


def test_allowlist_units_match_service_ids():
    """Every allowlist key must map to the unit the guest image actually installs."""
    expected_units = {
        "chute-log-shipper": "chute-log-shipper.service",
        "opa": "opa.service",
    }
    for service_id, unit in expected_units.items():
        assert SERVICE_ALLOWLIST[service_id].unit == unit
    # Keys and service_id fields must not drift apart — the id is the API path segment.
    for service_id, definition in SERVICE_ALLOWLIST.items():
        assert definition.service_id == service_id


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


def test_overview_ok_with_exited_oneshot_services(status_client, fake_runner):
    """Oneshot services report active/exited after success — overview must be ok."""
    fake_runner.set_response(
        "systemctl",
        CommandResult(
            exit_code=0,
            stdout=(
                "Id=setup-storage-bind-mounts.service\n"
                "LoadState=loaded\n"
                "ActiveState=active\n"
                "SubState=exited\n"
                "MainPID=0\n"
                "ExecMainStatus=0\n"
                "ExecMainCode=1\n"
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
    assert all(entry["healthy"] for entry in data["services"])


def test_oneshot_service_healthy_when_exited_zero(status_client, fake_runner):
    fake_runner.set_response(
        "systemctl",
        CommandResult(
            exit_code=0,
            stdout=(
                "Id=setup-storage-bind-mounts.service\n"
                "LoadState=loaded\n"
                "ActiveState=active\n"
                "SubState=exited\n"
                "MainPID=0\n"
                "ExecMainStatus=0\n"
                "ExecMainCode=1\n"
                "UnitFileState=enabled\n"
            ),
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/services/storage-bind-mounts/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"]["sub_state"] == "exited"
    assert data["status"]["exit_status"] == "0"
    assert data["healthy"] is True


def test_oneshot_service_unhealthy_when_exited_nonzero(status_client, fake_runner):
    fake_runner.set_response(
        "systemctl",
        CommandResult(
            exit_code=0,
            stdout=(
                "Id=setup-storage-bind-mounts.service\n"
                "LoadState=loaded\n"
                "ActiveState=active\n"
                "SubState=exited\n"
                "MainPID=0\n"
                "ExecMainStatus=1\n"
                "ExecMainCode=1\n"
                "UnitFileState=enabled\n"
            ),
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/services/storage-bind-mounts/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"]["sub_state"] == "exited"
    assert data["status"]["exit_status"] == "1"
    assert data["healthy"] is False


def test_fabricmanager_healthy_when_masked(status_client, fake_runner):
    """Masked nvidia-fabricmanager must be reported healthy (valid on non-NVLink hosts)."""
    fake_runner.set_response(
        "systemctl",
        CommandResult(
            exit_code=0,
            stdout=(
                "Id=nvidia-fabricmanager.service\n"
                "LoadState=masked\n"
                "ActiveState=inactive\n"
                "SubState=dead\n"
                "MainPID=0\n"
                "ExecMainStatus=0\n"
                "ExecMainCode=0\n"
                "UnitFileState=masked\n"
            ),
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/services/nvidia-fabricmanager/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"]["unit_file_state"] == "masked"
    assert data["healthy"] is True


def test_is_service_healthy_masked_with_masked_ok():
    """is_service_healthy returns True for a masked service when masked_ok=True."""
    from sek8s.system_manager.status.responses import ServiceStatus
    from sek8s.system_manager.status.util import is_service_healthy

    status = ServiceStatus(
        load_state="masked",
        active_state="inactive",
        sub_state="dead",
        unit_file_state="masked",
        main_pid="0",
        exit_code="0",
        exit_status="0",
    )
    assert is_service_healthy(status, masked_ok=True) is True


def test_is_service_healthy_masked_without_masked_ok():
    """is_service_healthy returns False for a masked service when masked_ok=False."""
    from sek8s.system_manager.status.responses import ServiceStatus
    from sek8s.system_manager.status.util import is_service_healthy

    status = ServiceStatus(
        load_state="masked",
        active_state="inactive",
        sub_state="dead",
        unit_file_state="masked",
        main_pid="0",
        exit_code="0",
        exit_status="0",
    )
    assert is_service_healthy(status, masked_ok=False) is False


def test_fabricmanager_has_masked_ok_set():
    """nvidia-fabricmanager ServiceDefinition must have masked_ok=True."""
    from sek8s.system_manager.status.models import SERVICE_ALLOWLIST

    assert SERVICE_ALLOWLIST["nvidia-fabricmanager"].masked_ok is True


def test_non_masked_ok_service_unhealthy_when_masked(status_client, fake_runner):
    """A service without masked_ok=True must be reported unhealthy when masked."""
    fake_runner.set_response(
        "systemctl",
        CommandResult(
            exit_code=0,
            stdout=(
                "Id=k3s.service\n"
                "LoadState=masked\n"
                "ActiveState=inactive\n"
                "SubState=dead\n"
                "MainPID=0\n"
                "ExecMainStatus=0\n"
                "ExecMainCode=0\n"
                "UnitFileState=masked\n"
            ),
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/services/k3s/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"]["unit_file_state"] == "masked"
    assert data["healthy"] is False


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


def test_disk_space_fails_when_du_produces_no_output(status_client, fake_runner):
    """A dead `du` must not read as an empty disk.

    Reproduces the production symptom: sudo was refused, `du` emitted nothing, and the
    endpoint still answered 200 with `total_size_bytes: 0` — indistinguishable from a
    genuinely empty tree, so a broken privileged path looked like a healthy VM.
    """
    fake_runner.set_response(
        "sudo",
        CommandResult(
            exit_code=1,
            stdout="",
            stderr="sudo: account validation failure, is your account locked?",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/disk/space?path=/")
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["error"] == "du_failed"
    assert detail["exit_code"] == 1
    assert "account validation failure" in detail["stderr"]


def test_disk_space_diagnostic_fails_when_du_produces_no_output(
    status_client, fake_runner
):
    """Diagnostic mode takes a separate code path and must fail the same way."""
    fake_runner.set_response(
        "sudo",
        CommandResult(
            exit_code=1,
            stdout="",
            stderr="sudo: a password is required",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/disk/space?path=/&diagnostic=true")
    assert response.status_code == 500
    assert response.json()["detail"]["error"] == "du_failed"


def test_disk_space_tolerates_partial_du_failure(status_client, fake_runner):
    """`du` exits 1 for any subtree it cannot read while still reporting the rest.

    That is the normal case walking `/` and must stay a 200 — otherwise a single
    unreadable directory takes out the whole report.
    """
    fake_runner.set_response(
        "sudo",
        CommandResult(
            exit_code=1,
            stdout="4096\t/var\n8192\t/opt\n12288\t/\n",
            stderr="du: cannot read directory '/proc/1/task': Permission denied",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )
    fake_runner.set_response(
        "df",
        CommandResult(
            exit_code=0,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/disk/space?path=/")
    assert response.status_code == 200
    data = response.json()
    assert data["total_size_bytes"] > 0
    assert {d["name"] for d in data["directories"]} == {"var", "opt"}


def test_logs_endpoint_fails_when_journalctl_fails(status_client, fake_runner):
    """A failed log fetch must not answer 200 with zero entries.

    Same bug class as the du case: an empty result is indistinguishable from a quiet
    service, so the endpoint you reach for to diagnose a broken VM reports nothing wrong.
    """
    fake_runner.set_response(
        "journalctl",
        CommandResult(
            exit_code=1,
            stdout="",
            stderr="Failed to open journal: Permission denied",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/services/k3s/logs")
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["error"] == "command_failed"
    assert detail["command"] == "journalctl"
    assert "Permission denied" in detail["stderr"]


def test_nvidia_smi_failure_is_reported_in_body_not_as_500(status_client, fake_runner):
    """nvidia-smi is reported, not consumed — its failure is a complete answer."""
    fake_runner.set_response(
        "nvidia-smi",
        CommandResult(
            exit_code=9,
            stdout="",
            stderr="NVIDIA-SMI has failed",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )

    response = status_client.get("/status/gpu/nvidia-smi")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["exit_code"] == 9
