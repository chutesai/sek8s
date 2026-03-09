"""Images submodule: request models and internal data types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PullStatusEnum(str, Enum):
    """Status for an image pull operation."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class PullRequest(BaseModel):
    """Request body for starting an image pull."""

    image_ref: str = Field(..., description="Image reference (e.g. localhost:30500/org/image:tag)")


@dataclass
class ImageEntry:
    """Single image entry from containerd (parsed from k3s ctr images list)."""

    ref: str
    digest: Optional[str]
    size_bytes: Optional[int]


@dataclass
class PullSnapshot:
    """Point-in-time state of an image pull."""

    image_ref: str
    status: PullStatusEnum
    error: Optional[str] = None
