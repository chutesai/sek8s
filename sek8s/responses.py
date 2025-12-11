from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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