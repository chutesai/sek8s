"""Helm submodule: API response models (JSON-serializable)."""

from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from .models import UpgradeStatusEnum


class UpgradeStartStatus(str, Enum):
    """Status returned by POST /helm/upgrade."""

    STARTED = "started"
    IN_PROGRESS = "in_progress"
    UP_TO_DATE = "up_to_date"


class UpgradeStartResponse(BaseModel):
    """Response for POST /helm/upgrade."""

    release: str = Field(..., description="Release name")
    status: UpgradeStartStatus = Field(
        ...,
        description="One of: started, in_progress, up_to_date",
    )


class UpgradeStatusResponse(BaseModel):
    """Response for GET /helm/upgrade/status."""

    release: str = Field(..., description="Release name")
    status: UpgradeStatusEnum = Field(
        ...,
        description="One of: in_progress, completed, failed",
    )
    error: Optional[str] = Field(None, description="Error message when status is failed")


class ReleaseListEntry(BaseModel):
    """Single release in the list response."""

    name: str = Field(..., description="Release name")
    namespace: str = Field(..., description="Namespace")
    chart: str = Field(..., description="Chart name and version (e.g. chutes-miner-gpu-0.2.1)")
    status: str = Field(..., description="Release status (e.g. deployed)")
    revision: int = Field(..., description="Revision number")
    updated: str = Field(..., description="Last updated timestamp")
    app_version: Optional[str] = Field(None, description="App version")


class ReleaseListResponse(BaseModel):
    """Response for GET /helm/releases."""

    releases: List[ReleaseListEntry] = Field(..., description="List of helm releases")


class ReleaseStatusResponse(BaseModel):
    """Response for GET /helm/releases/{name}/status."""

    info: Any = Field(..., description="Detailed release info from helm status")
    status: Optional[str] = Field(None, description="Release status")
    revision: Optional[int] = Field(None, description="Revision number")
    chart: Optional[str] = Field(None, description="Chart name and version")
    app_version: Optional[str] = Field(None, description="App version")
