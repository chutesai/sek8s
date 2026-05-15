"""Images submodule: API response models (JSON-serializable)."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ImageListEntry(BaseModel):
    """Single image in the list response."""

    ref: str = Field(..., description="Image reference")
    digest: Optional[str] = Field(None, description="Image digest (sha256:...)")
    size_bytes: Optional[int] = Field(None, description="Image size in bytes")


class ImageListResponse(BaseModel):
    """Response for GET /images."""

    images: List[ImageListEntry] = Field(
        ..., description="List of images in containerd"
    )


class PruneResponse(BaseModel):
    """Response for POST /images/prune."""

    status: str = Field(..., description="Prune status", examples=["completed"])
    removed_count: int = Field(0, description="Number of images removed")
    freed_bytes: int = Field(0, description="Bytes freed")
