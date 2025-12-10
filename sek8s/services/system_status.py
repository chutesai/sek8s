"""Read-only service exposing curated system status."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Query
from loguru import logger

from sek8s.config import SystemStatusConfig
from sek8s.server import WebServer


@dataclass(frozen=True)
class ServiceDefinition:
    service_id: str
    unit: str
    description: str


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool


SERVICE_ALLOWLIST: Dict[str, ServiceDefinition] = {
    "admission-controller": ServiceDefinition(
        service_id="admission-controller",
        unit="admission-controller.service",
        description="sek8s admission controller",
    ),
    "attestation-service": ServiceDefinition(
        service_id="attestation-service",
        unit="attestation-service.service",
        description="TDX/nvtrust attestation service",
    ),
    "k3s": ServiceDefinition(
        service_id="k3s",
        unit="k3s.service",
        description="Lightweight Kubernetes control plane",
    ),
    "nvidia-persistenced": ServiceDefinition(
        service_id="nvidia-persistenced",
        unit="nvidia-persistenced.service",
        description="NVIDIA persistence daemon",
    ),
    "nvidia-fabricmanager": ServiceDefinition(
        service_id="nvidia-fabricmanager",
        unit="nvidia-fabricmanager.service",
        description="NVIDIA fabric manager",
    ),
}


def _parse_key_value(output: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key] = value
    return parsed


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


async def _run_command(command: List[str], timeout: float, limit: int) -> CommandResult:
    logger.debug("Executing command: {}", command)
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        logger.error("Command timeout for {}", command)
        raise HTTPException(
            status_code=504,
            detail={"error": "timeout", "command": command[0]},
        ) from exc
    except FileNotFoundError as exc:
        logger.error("Binary not found for {}", command)
        raise HTTPException(
            status_code=503,
            detail={"error": "missing_binary", "binary": command[0]},
        ) from exc

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    stdout, stdout_truncated = _truncate(stdout, limit)
    stderr, stderr_truncated = _truncate(stderr, limit)

    return CommandResult(
        exit_code=process.returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def _ensure_success(result: CommandResult, command_name: str) -> None:
    if result.exit_code == 0:
        return

    logger.error("Command {} failed with exit code {}", command_name, result.exit_code)
    raise HTTPException(
        status_code=502,
        detail={
            "error": "command_failed",
            "command": command_name,
            "exit_code": result.exit_code,
            "stderr": result.stderr,
            "stderr_truncated": result.stderr_truncated,
        },
    )


class SystemStatusServer(WebServer):
    """FastAPI server exposing read-only system state."""

    def __init__(self, config: SystemStatusConfig):
        self.config = config
        super().__init__(config)

    def _setup_routes(self) -> None:
        self.app.add_api_route("/health", self.health, methods=["GET"])
        self.app.add_api_route("/services", self.list_services, methods=["GET"])
        self.app.add_api_route(
            "/services/{service_id}/status",
            self.get_service_status,
            methods=["GET"],
        )
        self.app.add_api_route(
            "/services/{service_id}/logs",
            self.get_service_logs,
            methods=["GET"],
        )
        self.app.add_api_route(
            "/gpu/nvidia-smi",
            self.nvidia_smi,
            methods=["GET"],
        )
        self.app.add_api_route(
            "/overview",
            self.overview,
            methods=["GET"],
        )

    async def health(self) -> Dict[str, str]:
        return {"status": "ok"}

    async def list_services(self) -> Dict[str, List[Dict[str, str]]]:
        return {
            "services": [
                {
                    "id": service.service_id,
                    "unit": service.unit,
                    "description": service.description,
                }
                for service in SERVICE_ALLOWLIST.values()
            ]
        }

    async def overview(self) -> Dict[str, Any]:
        services = await asyncio.gather(
            *(
                self._collect_service_status(service, tolerate_errors=True)
                for service in SERVICE_ALLOWLIST.values()
            )
        )

        gpu_info = await self.nvidia_smi(detail=False, gpu="all")
        gpu_healthy = gpu_info.get("status") == "ok"
        services_healthy = all(entry.get("healthy") for entry in services)
        overall_status = "ok" if services_healthy and gpu_healthy else "degraded"

        return {
            "status": overall_status,
            "services": services,
            "gpu": gpu_info,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def get_service_status(self, service_id: str) -> Dict[str, object]:
        service = self._resolve_service(service_id)
        return await self._collect_service_status(service)

    async def _collect_service_status(
        self,
        service: ServiceDefinition,
        *,
        tolerate_errors: bool = False,
    ) -> Dict[str, Any]:
        properties = [
            "Id",
            "LoadState",
            "ActiveState",
            "SubState",
            "MainPID",
            "ExecMainStatus",
            "ExecMainCode",
            "UnitFileState",
        ]
        command = [
            "systemctl",
            "show",
            service.unit,
            "--no-pager",
        ] + [f"--property={prop}" for prop in properties]

        try:
            result = await _run_command(command, self.config.command_timeout_seconds, self.config.max_output_bytes)
            _ensure_success(result, "systemctl")
        except HTTPException as exc:
            if tolerate_errors:
                return {
                    "service": {
                        "id": service.service_id,
                        "unit": service.unit,
                        "description": service.description,
                    },
                    "status": None,
                    "healthy": False,
                    "error": exc.detail,
                }
            raise

        data = _parse_key_value(result.stdout)
        status = {
            "load_state": data.get("LoadState"),
            "active_state": data.get("ActiveState"),
            "sub_state": data.get("SubState"),
            "unit_file_state": data.get("UnitFileState"),
            "main_pid": data.get("MainPID"),
            "exit_code": data.get("ExecMainCode"),
            "exit_status": data.get("ExecMainStatus"),
        }

        return {
            "service": {
                "id": service.service_id,
                "unit": service.unit,
                "description": service.description,
            },
            "status": status,
            "healthy": self._is_service_healthy(status),
        }

    async def get_service_logs(
        self,
        service_id: str,
        lines: int = Query(200, ge=1),
        since_minutes: Optional[int] = Query(None, ge=1, le=1440),
    ) -> Dict[str, object]:
        service = self._resolve_service(service_id)

        max_lines = self.config.log_tail_max
        default_lines = self.config.log_tail_default
        clamped_lines = max(1, min(lines or default_lines, max_lines))

        command = [
            "journalctl",
            f"--unit={service.unit}",
            "--no-pager",
            "--output=short",
            f"--lines={clamped_lines}",
        ]

        if since_minutes:
            window_limit = min(since_minutes, self.config.log_window_max_minutes)
            since_time = datetime.now(timezone.utc) - timedelta(minutes=window_limit)
            command.append(f"--since={since_time.isoformat()}")

        result = await _run_command(command, self.config.command_timeout_seconds, self.config.max_output_bytes)
        _ensure_success(result, "journalctl")

        entries = [line for line in result.stdout.splitlines() if line]

        return {
            "service": {
                "id": service.service_id,
                "unit": service.unit,
            },
            "requested_lines": lines,
            "returned_lines": len(entries),
            "stdout_truncated": result.stdout_truncated,
            "logs": entries,
        }

    async def nvidia_smi(
        self,
        detail: bool = Query(False, description="Return detailed (-q) output"),
        gpu: str = Query("all", description="GPU index or 'all'"),
    ) -> Dict[str, object]:
        command = ["nvidia-smi"]
        if detail:
            command.append("-q")

        if gpu != "all":
            try:
                gpu_index = int(gpu)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="gpu must be an integer or 'all'") from exc
            if gpu_index < 0:
                raise HTTPException(status_code=400, detail="gpu must be non-negative")
            command.extend(["-i", str(gpu_index)])

        result = await _run_command(command, self.config.command_timeout_seconds, self.config.max_output_bytes)

        status_code = 200 if result.exit_code == 0 else 502
        stdout_lines = result.stdout.splitlines()
        stderr_lines = result.stderr.splitlines()
        return {
            "command": command,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "stdout_lines": stdout_lines,
            "stderr_lines": stderr_lines,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
            "detail": detail,
            "gpu": gpu,
            "status": "ok" if status_code == 200 else "error",
        }

    def _resolve_service(self, service_id: str) -> ServiceDefinition:
        if service_id not in SERVICE_ALLOWLIST:
            raise HTTPException(status_code=404, detail="service not allowed")
        return SERVICE_ALLOWLIST[service_id]

    def _is_service_healthy(self, status: Dict[str, Optional[str]]) -> bool:
        return (
            status.get("load_state") == "loaded"
            and status.get("active_state") == "active"
            and status.get("sub_state") in {"running", "listening", None}
        )


def run() -> None:
    config = SystemStatusConfig()
    server = SystemStatusServer(config)
    server.run()

if __name__ == "__main__":
    run()