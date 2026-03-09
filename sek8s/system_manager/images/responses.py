"""Images submodule: API response models (JSON-serializable)."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from .models import PullStatusEnum


class PullStartStatus(str, Enum):
    """Status returned by POST /images/pull."""

    STARTED = "started"
    IN_PROGRESS = "in_progress"
    PRESENT = "present"


class ImageListEntry(BaseModel):
    """Single image in the list response."""

    ref: str = Field(..., description="Image reference")
    digest: Optional[str] = Field(None, description="Image digest (sha256:...)")
    size_bytes: Optional[int] = Field(None, description="Image size in bytes")


class ImageListResponse(BaseModel):
    """Response for GET /images."""

    images: List[ImageListEntry] = Field(..., description="List of images in containerd")


class PullStartResponse(BaseModel):
    """Response for POST /images/pull."""

    image_ref: str = Field(..., description="Image reference")
    status: PullStartStatus = Field(
        ...,
        description="One of: started, in_progress, present",
    )


class PullStatusEntry(BaseModel):
    """Status for a single image pull."""

    image_ref: str = Field(..., description="Image reference")
    status: PullStatusEnum = Field(
        ...,
        description="One of: pending, in_progress, completed, failed",
    )
    error: Optional[str] = Field(None, description="Error message when status is failed")


class PullStatusResponse(BaseModel):
    """Response for GET /images/pull/status."""

    pulls: List[PullStatusEntry] = Field(..., description="Status per image pull")


class PruneResponse(BaseModel):
    """Response for POST /images/prune."""

    status: str = Field(..., description="Prune status", example="completed")
    removed_count: int = Field(0, description="Number of images removed")
    freed_bytes: int = Field(0, description="Bytes freed")
