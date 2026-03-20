"""Helm submodule: API response models (JSON-serializable)."""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field

from .models import ReleaseStatusDetail, UpgradeStartStatus, UpgradeStatusEnum


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


class ReleaseStatusInfo(BaseModel):
    """Structured release status from helm status (nested under info)."""

    status: Optional[str] = Field(None, description="Release status")
    revision: Optional[int] = Field(None, description="Revision number")
    chart: Optional[str] = Field(None, description="Chart name and version")
    app_version: Optional[str] = Field(None, description="App version")

    @classmethod
    def from_detail(cls, detail: ReleaseStatusDetail) -> ReleaseStatusInfo:
        """Build ReleaseStatusInfo from ReleaseStatusDetail."""
        return cls(
            status=detail.status,
            revision=detail.revision,
            chart=detail.chart,
            app_version=detail.app_version,
        )


class ReleaseStatusResponse(BaseModel):
    """Response for GET /helm/releases/{name}/status."""

    info: ReleaseStatusInfo = Field(..., description="Structured release status from helm")
    status: Optional[str] = Field(None, description="Release status")
    revision: Optional[int] = Field(None, description="Revision number")
    chart: Optional[str] = Field(None, description="Chart name and version")
    app_version: Optional[str] = Field(None, description="App version")

    @classmethod
    def from_detail(cls, detail: ReleaseStatusDetail) -> ReleaseStatusResponse:
        """Build ReleaseStatusResponse from ReleaseStatusDetail."""
        info = ReleaseStatusInfo.from_detail(detail)
        return cls(
            info=info,
            status=detail.status,
            revision=detail.revision,
            chart=detail.chart,
            app_version=detail.app_version,
        )
