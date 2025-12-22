"""Read-only service exposing curated system status."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiocache import cached as aiocache_cached
from fastapi import HTTPException, Query
from loguru import logger

from sek8s.config import SystemStatusConfig
from sek8s.responses import (
    DirectoryInfo,
    DiskSpaceResponse,
    HealthResponse,
    NvidiaSmiResponse,
    OverviewResponse,
    ServiceInfo,
    ServiceLogsResponse,
    ServicesListResponse,
    ServiceStatus,
    ServiceStatusResponse,
)
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
        self.app.add_api_route(
            "/health",
            self.health,
            methods=["GET"],
            response_model=HealthResponse,
            summary="Health check",
            description="Returns OK if service is running",
        )
        self.app.add_api_route(
            "/services",
            self.list_services,
            methods=["GET"],
            response_model=ServicesListResponse,
            summary="List available services",
            description="Returns list of all services that can be monitored",
        )
        self.app.add_api_route(
            "/services/{service_id}/status",
            self.get_service_status,
            methods=["GET"],
            response_model=ServiceStatusResponse,
            summary="Get service status",
            description="Returns systemd status for a specific service",
        )
        self.app.add_api_route(
            "/services/{service_id}/logs",
            self.get_service_logs,
            methods=["GET"],
            response_model=ServiceLogsResponse,
            summary="Get service logs",
            description="Returns recent journal logs for a specific service",
        )
        self.app.add_api_route(
            "/gpu/nvidia-smi",
            self.nvidia_smi,
            methods=["GET"],
            response_model=NvidiaSmiResponse,
            summary="Get GPU status",
            description="Returns nvidia-smi output for GPUs",
        )
        self.app.add_api_route(
            "/overview",
            self.overview,
            methods=["GET"],
            response_model=OverviewResponse,
            summary="System overview",
            description="Returns combined status of all services and GPUs",
        )
        self.app.add_api_route(
            "/disk/space",
            self.get_disk_space,
            methods=["GET"],
            response_model=DiskSpaceResponse,
            summary="Get directory sizes",
            description="Returns sizes of immediate subdirectories within a given path",
        )

    @aiocache_cached(ttl=30)
    async def health(self) -> HealthResponse:
        return HealthResponse(status="ok")

    @aiocache_cached(ttl=30)
    async def list_services(self) -> ServicesListResponse:
        return ServicesListResponse(
            services=[
                ServiceInfo(
                    id=service.service_id,
                    unit=service.unit,
                    description=service.description,
                )
                for service in SERVICE_ALLOWLIST.values()
            ]
        )

    @aiocache_cached(ttl=30)
    async def overview(self) -> OverviewResponse:
        services = await asyncio.gather(
            *(
                self._collect_service_status(service, tolerate_errors=True)
                for service in SERVICE_ALLOWLIST.values()
            )
        )

        gpu_info = await self.nvidia_smi(detail=False, gpu="all")
        gpu_healthy = gpu_info.status == "ok"
        services_healthy = all(entry.healthy for entry in services)
        overall_status = "ok" if services_healthy and gpu_healthy else "degraded"

        return OverviewResponse(
            status=overall_status,
            services=services,
            gpu=gpu_info,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @aiocache_cached(ttl=30)
    async def get_service_status(self, service_id: str) -> ServiceStatusResponse:
        service = self._resolve_service(service_id)
        return await self._collect_service_status(service)

    async def _collect_service_status(
        self,
        service: ServiceDefinition,
        *,
        tolerate_errors: bool = False,
    ) -> ServiceStatusResponse:
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
                return ServiceStatusResponse(
                    service=ServiceInfo(
                        id=service.service_id,
                        unit=service.unit,
                        description=service.description,
                    ),
                    status=None,
                    healthy=False,
                    error=exc.detail,
                )
            raise

        data = _parse_key_value(result.stdout)
        status = ServiceStatus(
            load_state=data.get("LoadState"),
            active_state=data.get("ActiveState"),
            sub_state=data.get("SubState"),
            unit_file_state=data.get("UnitFileState"),
            main_pid=data.get("MainPID"),
            exit_code=data.get("ExecMainCode"),
            exit_status=data.get("ExecMainStatus"),
        )

        return ServiceStatusResponse(
            service=ServiceInfo(
                id=service.service_id,
                unit=service.unit,
                description=service.description,
            ),
            status=status,
            healthy=self._is_service_healthy(status),
        )

    @aiocache_cached(ttl=30)
    async def get_service_logs(
        self,
        service_id: str,
        lines: int = Query(200, ge=1),
        since_minutes: Optional[int] = Query(None, ge=1, le=1440),
    ) -> ServiceLogsResponse:
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

        return ServiceLogsResponse(
            service={
                "id": service.service_id,
                "unit": service.unit,
            },
            requested_lines=lines,
            returned_lines=len(entries),
            stdout_truncated=result.stdout_truncated,
            logs=entries,
        )

    @aiocache_cached(ttl=60)
    async def nvidia_smi(
        self,
        detail: bool = Query(False, description="Return detailed (-q) output"),
        gpu: str = Query("all", description="GPU index or 'all'"),
    ) -> NvidiaSmiResponse:
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
        return NvidiaSmiResponse(
            command=command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            detail=detail,
            gpu=gpu,
            status="ok" if status_code == 200 else "error",
        )

    def _resolve_service(self, service_id: str) -> ServiceDefinition:
        if service_id not in SERVICE_ALLOWLIST:
            raise HTTPException(status_code=404, detail="service not allowed")
        return SERVICE_ALLOWLIST[service_id]

    def _is_service_healthy(self, status: ServiceStatus) -> bool:
        return (
            status.load_state == "loaded"
            and status.active_state == "active"
            and status.sub_state in {"running", "listening", None}
        )

    def _validate_path(self, path: str) -> Path:
        """Validate and resolve path, ensuring it's safe to query."""
        try:
            resolved = Path(path).resolve()
        except (ValueError, RuntimeError) as exc:
            logger.error("Invalid path: {}", path)
            raise HTTPException(status_code=400, detail="Invalid path") from exc

        if not resolved.exists():
            raise HTTPException(status_code=404, detail="Path does not exist")

        if not resolved.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")

        return resolved

    def _parse_du_line(self, line: str) -> tuple[int, str]:
        """Parse a line from du output: size<tab>path."""
        parts = line.split("\t", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid du output line: {line}")
        try:
            size_kb = int(parts[0])
        except ValueError as exc:
            raise ValueError(f"Invalid size in du output: {parts[0]}") from exc
        return size_kb * 1024, parts[1]

    def _human_readable_size(self, size_bytes: int) -> str:
        """Convert bytes to human-readable format."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"

    @aiocache_cached(ttl=60)
    async def get_disk_space(
        self,
        path: str = Query("/", description="Directory path to analyze"),
    ) -> DiskSpaceResponse:
        """Get sizes of immediate subdirectories within the given path."""
        validated_path = self._validate_path(path)

        # Use du to get directory sizes (immediate subdirectories only)
        # -s: summarize (don't recurse into subdirectories)
        # -k: output in kilobytes
        # --max-depth=1: only immediate children
        command = [
            "du",
            "-sk",
            "--max-depth=1",
            str(validated_path),
        ]

        result = await _run_command(
            command,
            self.config.command_timeout_seconds,
            self.config.max_output_bytes,
        )
        _ensure_success(result, "du")

        directories: List[DirectoryInfo] = []
        parent_size = 0

        for line in result.stdout.strip().splitlines():
            if not line:
                continue

            try:
                size_bytes, dir_path = self._parse_du_line(line)
            except ValueError as exc:
                logger.warning("Failed to parse du line: {}", exc)
                continue

            # Skip the parent directory itself (it appears in the output)
            if Path(dir_path).resolve() == validated_path:
                parent_size = size_bytes
                continue

            dir_name = Path(dir_path).name
            directories.append(
                DirectoryInfo(
                    name=dir_name,
                    size_bytes=size_bytes,
                    size_human=self._human_readable_size(size_bytes),
                )
            )

        # Sort by size descending
        directories.sort(key=lambda d: d.size_bytes, reverse=True)

        # Calculate total (use parent_size if available, otherwise sum subdirs)
        total_bytes = parent_size if parent_size > 0 else sum(d.size_bytes for d in directories)

        return DiskSpaceResponse(
            path=str(validated_path),
            directories=directories,
            total_size_bytes=total_bytes,
            total_size_human=self._human_readable_size(total_bytes),
            stdout_truncated=result.stdout_truncated,
        )


def run() -> None:
    config = SystemStatusConfig()
    server = SystemStatusServer(config)
    server.run()

if __name__ == "__main__":
    run()