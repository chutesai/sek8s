from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from sek8s.system_manager.cache.models import CacheChuteStatusEnum


class CacheDownloadStatus(str, Enum):
    """Status returned by the download (POST) endpoint."""

    STARTED = "started"
    PRESENT = "present"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"


class AttestationResponse(BaseModel):

    tdx_quote: str = Field(..., description="")

    nvtrust_evidence: str = Field(..., description="")


# System Status API Response Models


class HealthResponse(BaseModel):
    status: str = Field(..., description="Health status", example="ok")


class ServiceInfo(BaseModel):
    id: str = Field(..., description="Service identifier")
    unit: str = Field(..., description="Systemd unit name")
    description: str = Field(..., description="Service description")


class ServicesListResponse(BaseModel):
    services: List[ServiceInfo] = Field(..., description="List of available services")


class ServiceStatus(BaseModel):
    load_state: Optional[str] = Field(None, description="Systemd LoadState")
    active_state: Optional[str] = Field(None, description="Systemd ActiveState")
    sub_state: Optional[str] = Field(None, description="Systemd SubState")
    unit_file_state: Optional[str] = Field(None, description="Systemd UnitFileState")
    main_pid: Optional[str] = Field(None, description="Main process PID")
    exit_code: Optional[str] = Field(None, description="Exit code type")
    exit_status: Optional[str] = Field(None, description="Exit status value")


class ServiceStatusResponse(BaseModel):
    service: ServiceInfo
    status: Optional[ServiceStatus] = Field(None, description="Service status details")
    healthy: bool = Field(..., description="Whether service is healthy")
    error: Optional[Dict[str, Any]] = Field(None, description="Error details if status check failed")


class ServiceLogsResponse(BaseModel):
    service: Dict[str, str] = Field(..., description="Service identifier and unit")
    requested_lines: int = Field(..., description="Number of log lines requested")
    returned_lines: int = Field(..., description="Number of log lines returned")
    stdout_truncated: bool = Field(..., description="Whether output was truncated")
    logs: List[str] = Field(..., description="Log entries")


class NvidiaSmiResponse(BaseModel):
    command: List[str] = Field(..., description="Command executed")
    exit_code: int = Field(..., description="Command exit code")
    stdout: str = Field(..., description="Standard output")
    stderr: str = Field(..., description="Standard error")
    stdout_lines: List[str] = Field(..., description="Standard output split into lines")
    stderr_lines: List[str] = Field(..., description="Standard error split into lines")
    stdout_truncated: bool = Field(..., description="Whether stdout was truncated")
    stderr_truncated: bool = Field(..., description="Whether stderr was truncated")
    detail: bool = Field(..., description="Whether detailed output was requested")
    gpu: str = Field(..., description="GPU index or 'all'")
    status: str = Field(..., description="Status of the command", example="ok")


class OverviewResponse(BaseModel):
    status: str = Field(..., description="Overall system status", example="ok")
    services: List[ServiceStatusResponse] = Field(..., description="Status of all monitored services")
    gpu: NvidiaSmiResponse = Field(..., description="GPU status from nvidia-smi")
    timestamp: str = Field(..., description="ISO 8601 timestamp of the report")


class DirectoryInfo(BaseModel):
    name: str = Field(..., description="Directory name")
    path: str = Field(..., description="Full directory path")
    size_bytes: int = Field(..., description="Total size in bytes")
    size_human: str = Field(..., description="Human-readable size")
    depth: int = Field(..., description="Depth level from root path")
    percentage: Optional[float] = Field(None, description="Percentage of total disk usage")


class DiskSpaceResponse(BaseModel):
    path: str = Field(..., description="Parent directory path")
    directories: List[DirectoryInfo] = Field(..., description="List of immediate subdirectories with sizes")
    total_size_bytes: int = Field(..., description="Total size of all directories in bytes")
    total_size_human: str = Field(..., description="Total size in human-readable format")
    stdout_truncated: bool = Field(..., description="Whether output was truncated")
    diagnostic_mode: bool = Field(False, description="Whether diagnostic mode was enabled")
    max_depth: Optional[int] = Field(None, description="Maximum depth analyzed in diagnostic mode")
    top_n: Optional[int] = Field(None, description="Number of top offenders shown per level")


class ShutdownResponse(BaseModel):
    status: str = Field(..., description="Shutdown status", example="initiated")
    message: str = Field(..., description="Shutdown message")
    timestamp: str = Field(..., description="ISO 8601 timestamp of shutdown request")


# Cache API Response Models (all JSON-serializable; no Path)


class CacheDownloadResponse(BaseModel):
    chute_id: str = Field(..., description="Chute ID")
    status: CacheDownloadStatus = Field(
        ...,
        description="One of: started, present, in_progress, failed",
    )


class CacheChuteStatus(BaseModel):
    chute_id: str = Field(..., description="Chute ID")
    status: CacheChuteStatusEnum = Field(
        ...,
        description="One of: in_progress, present, missing",
    )
    percent_complete: Optional[float] = Field(
        None,
        description="Download progress 0-100 when in_progress and total size is known; omitted otherwise",
    )
    repo_id: Optional[str] = Field(None, description="HF repo ID when present or in_progress")
    revision: Optional[str] = Field(None, description="Revision when present or in_progress")
    size_bytes: Optional[int] = Field(None, description="Size in bytes when present")


class CacheDownloadStatusResponse(BaseModel):
    chutes: List[CacheChuteStatus] = Field(..., description="Status per chute")


class CacheOverviewEntry(BaseModel):
    chute_id: str = Field(..., description="Chute ID")
    repo_id: str = Field(..., description="HF repo ID")
    revision: Optional[str] = Field(None, description="Revision")
    size_bytes: int = Field(..., description="Size in bytes")
    last_accessed: Optional[float] = Field(None, description="Last access time (Unix)")

class CacheOverviewResponse(BaseModel):
    total_size_bytes: int = Field(..., description="Total cache size in bytes")
    chutes: List[CacheOverviewEntry] = Field(..., description="Entries per chute")


class CacheCleanupResponse(BaseModel):
    status: str = Field(..., description="Cleanup status", example="completed")
    freed_bytes: int = Field(0, description="Bytes freed")
    removed_chutes: List[str] = Field(default_factory=list, description="Chute IDs removed")