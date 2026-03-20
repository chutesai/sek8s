"""Helm submodule: request models and internal data types."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class UpgradeStatusEnum(str, Enum):
    """Status for an async helm upgrade operation."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class UpgradeStartStatus(str, Enum):
    """Result of starting a helm upgrade (POST /helm/upgrade)."""

    STARTED = "started"
    IN_PROGRESS = "in_progress"
    UP_TO_DATE = "up_to_date"


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

    @classmethod
    def from_dict(cls, item: dict) -> ReleaseEntry:
        """Build a ReleaseEntry from a single helm list item (dict)."""
        return cls(
            name=item.get("name") or "",
            namespace=item.get("namespace") or "",
            chart=item.get("chart") or "",
            status=item.get("status") or "",
            revision=int(item.get("revision", 0)),
            updated=item.get("updated", ""),
            app_version=item.get("app_version"),
        )

    @classmethod
    def from_list_json(cls, json_str: str) -> List[ReleaseEntry]:
        """Build a list of ReleaseEntry from helm list -o json output."""
        data = json.loads(json_str)
        if not isinstance(data, list):
            raise ValueError("Expected JSON array")
        entries: List[ReleaseEntry] = []
        for item in data:
            if isinstance(item, dict):
                entries.append(cls.from_dict(item))
        return entries


@dataclass
class ReleaseStatusDetail:
    """Parsed helm status output for a release."""

    status: Optional[str] = None
    revision: Optional[int] = None
    chart: Optional[str] = None
    app_version: Optional[str] = None

    @classmethod
    def from_json(cls, json_str: str) -> ReleaseStatusDetail:
        """Build a ReleaseStatusDetail from helm status -o json output."""
        data = json.loads(json_str)
        if not isinstance(data, dict):
            raise ValueError("Expected JSON object")
        info = data.get("info") or {}
        status = data.get("status") or info.get("status")
        revision = data.get("revision") or info.get("revision")
        chart = data.get("chart")
        app_version = data.get("app_version")
        return cls(
            status=status,
            revision=int(revision) if revision is not None else None,
            chart=chart,
            app_version=app_version,
        )


@dataclass
class UpgradeSnapshot:
    """Point-in-time state of an async helm upgrade."""

    release: str
    status: UpgradeStatusEnum
    error: Optional[str] = None
