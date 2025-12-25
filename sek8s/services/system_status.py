"""Read-only service exposing curated system status."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiocache import cached as aiocache_cached
from fastapi import Depends, HTTPException, Query
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
    ShutdownResponse,
)
from sek8s.server import WebServer
from sek8s.services.util import authorize


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
        self.app.add_api_route(
            "/system/shutdown",
            self.shutdown_system,
            methods=["POST"],
            response_model=ShutdownResponse,
            summary="Graceful system shutdown",
            description="Initiates a graceful system shutdown (requires miner authentication)",
            dependencies=[Depends(authorize(allow_miner=True, purpose="/system/shutdown"))],
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

    async def get_disk_space(
        self,
        path: str = Query("/", description="Directory path to analyze"),
        diagnostic: bool = Query(False, description="Enable diagnostic mode for deep analysis"),
        max_depth: int = Query(3, ge=1, le=10, description="Maximum depth for diagnostic mode (default: 3)"),
        top_n: int = Query(10, ge=1, le=100, description="Show top N directories per level (default: 10)"),
    ) -> DiskSpaceResponse:
        """Get sizes of subdirectories within the given path.
        
        Standard mode: Shows immediate subdirectories only.
        Diagnostic mode: Recursively analyzes up to max_depth levels and shows top N offenders.
        The max_depth and top_n parameters are only used when diagnostic=true.
        """
        # Extract actual values from Query objects if needed (for direct calls in tests)
        actual_path = path.default if hasattr(path, 'default') else path
        actual_diagnostic = diagnostic.default if hasattr(diagnostic, 'default') else diagnostic
        actual_max_depth = max_depth.default if hasattr(max_depth, 'default') else max_depth
        actual_top_n = top_n.default if hasattr(top_n, 'default') else top_n
        
        validated_path = self._validate_path(actual_path)

        if actual_diagnostic:
            return await self._get_disk_space_diagnostic(
                validated_path, actual_max_depth, actual_top_n
            )
        else:
            return await self._get_disk_space_simple(validated_path)

    async def _get_disk_space_simple(
        self, validated_path: Path
    ) -> DiskSpaceResponse:
        """Get sizes of immediate subdirectories only."""
        # Use du to get directory sizes (immediate subdirectories only)
        # -k: output in kilobytes
        # --max-depth=1: only immediate children
        # Note: Don't use -s with --max-depth as they conflict
        command = [
            "du",
            "-k",
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
                    path=dir_path,
                    size_bytes=size_bytes,
                    size_human=self._human_readable_size(size_bytes),
                    depth=1,
                    percentage=None,
                )
            )

        # Sort by size descending
        directories.sort(key=lambda d: d.size_bytes, reverse=True)

        # Calculate total (use parent_size if available, otherwise sum subdirs)
        total_bytes = parent_size if parent_size > 0 else sum(d.size_bytes for d in directories)

        # Add percentages
        if total_bytes > 0:
            for d in directories:
                d.percentage = (d.size_bytes / total_bytes) * 100

        return DiskSpaceResponse(
            path=str(validated_path),
            directories=directories,
            total_size_bytes=total_bytes,
            total_size_human=self._human_readable_size(total_bytes),
            stdout_truncated=result.stdout_truncated,
            diagnostic_mode=False,
        )

    async def _get_disk_space_diagnostic(
        self, validated_path: Path, max_depth: int, top_n: int
    ) -> DiskSpaceResponse:
        """Recursive analysis to find worst disk space offenders at multiple depth levels."""
        # Use du with specified max-depth to get all directories up to that level
        # This gives us a complete picture of disk usage at all levels
        command = [
            "du",
            "-k",
            f"--max-depth={max_depth}",
            str(validated_path),
        ]

        result = await _run_command(
            command,
            self.config.command_timeout_seconds * 3,  # Allow more time for deep scans
            self.config.max_output_bytes * 2,  # Allow larger output
        )
        _ensure_success(result, "du")

        # Parse all directory entries
        all_entries: List[tuple[int, str, int]] = []  # (size_bytes, path, depth)
        root_size = 0

        for line in result.stdout.strip().splitlines():
            if not line:
                continue

            try:
                size_bytes, dir_path = self._parse_du_line(line)
            except ValueError as exc:
                logger.warning("Failed to parse du line: {}", exc)
                continue

            resolved = Path(dir_path).resolve()
            
            # Calculate depth relative to validated_path
            try:
                relative = resolved.relative_to(validated_path)
                depth = len(relative.parts) if str(relative) != "." else 0
            except ValueError:
                # Path is not relative to validated_path (shouldn't happen)
                continue

            if depth == 0:
                root_size = size_bytes
                continue

            all_entries.append((size_bytes, str(resolved), depth))

        # Group by depth and get top N for each level
        depth_groups: Dict[int, List[tuple[int, str]]] = {}
        for size_bytes, path, depth in all_entries:
            if depth not in depth_groups:
                depth_groups[depth] = []
            depth_groups[depth].append((size_bytes, path))

        # Get top N from each depth level
        top_offenders: List[DirectoryInfo] = []
        for depth in sorted(depth_groups.keys()):
            entries = depth_groups[depth]
            entries.sort(reverse=True)  # Sort by size descending
            
            for size_bytes, path in entries[:top_n]:
                percentage = (size_bytes / root_size * 100) if root_size > 0 else 0
                top_offenders.append(
                    DirectoryInfo(
                        name=Path(path).name,
                        path=path,
                        size_bytes=size_bytes,
                        size_human=self._human_readable_size(size_bytes),
                        depth=depth,
                        percentage=percentage,
                    )
                )

        # Sort all results by size descending for final output
        top_offenders.sort(key=lambda d: d.size_bytes, reverse=True)

        return DiskSpaceResponse(
            path=str(validated_path),
            directories=top_offenders,
            total_size_bytes=root_size,
            total_size_human=self._human_readable_size(root_size),
            stdout_truncated=result.stdout_truncated,
            diagnostic_mode=True,
            max_depth=max_depth,
            top_n=top_n,
        )

    async def shutdown_system(self) -> ShutdownResponse:
        """Initiate a graceful system shutdown.
        
        This endpoint requires miner authentication via signed message.
        It triggers a graceful shutdown using the shutdown command.
        Note: Requires sudoers configuration for the status user.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        
        logger.warning("Graceful shutdown requested at {}", timestamp)
        
        # Schedule the shutdown command to run after we return the response
        # Use 'shutdown -h now' for graceful shutdown
        # Requires: status ALL=(ALL) NOPASSWD: /sbin/shutdown
        async def delayed_shutdown():
            await asyncio.sleep(2)  # Give time for response to be sent
            logger.critical("Executing graceful shutdown...")
            try:
                process = await asyncio.create_subprocess_exec(
                    "sudo", "/sbin/shutdown", "-h", "now",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await process.communicate()
            except Exception as e:
                logger.error("Failed to execute shutdown: {}", e)
        
        # Schedule shutdown in background
        asyncio.create_task(delayed_shutdown())
        
        return ShutdownResponse(
            status="initiated",
            message="Graceful shutdown initiated. System will power off in 2 seconds.",
            timestamp=timestamp,
        )


def run() -> None:
    config = SystemStatusConfig()
    server = SystemStatusServer(config)
    server.run()

if __name__ == "__main__":
    run()