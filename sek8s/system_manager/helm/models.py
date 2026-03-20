"""Helm submodule: request models and internal data types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class UpgradeStatusEnum(str, Enum):
    """Status for an async helm upgrade operation."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class UpgradeRequest(BaseModel):
    """Request body for starting a helm upgrade."""

    release: str = Field(..., description="Release name (must be in allowlist, e.g. chutes)")
    version: Optional[str] = Field(
        None,
        description="Chart version (optional; defaults to latest from repo)",
    )


@dataclass
class ReleaseEntry:
    """Single helm release from helm list (parsed from k3s-helm-helper list)."""

    name: str
    namespace: str
    chart: str
    status: str
    revision: int
    updated: str
    app_version: Optional[str] = None


@dataclass
class UpgradeSnapshot:
    """Point-in-time state of an async helm upgrade."""

    release: str
    status: UpgradeStatusEnum
    error: Optional[str] = None
