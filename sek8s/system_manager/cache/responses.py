"""Cache submodule: API response models (JSON-serializable)."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from .models import CacheChuteStatusEnum


class CacheDownloadStatus(str, Enum):
    """Status returned by the download (POST) endpoint."""

    STARTED = "started"
    PRESENT = "present"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"


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
        description="One of: in_progress, present, missing, failed",
    )
    percent_complete: Optional[float] = Field(
        None,
        description="Download progress 0-100 when in_progress and total size is known; omitted otherwise",
    )
    repo_id: Optional[str] = Field(None, description="HF repo ID when present or in_progress")
    revision: Optional[str] = Field(None, description="Revision when present or in_progress")
    size_bytes: Optional[int] = Field(None, description="Size in bytes when present")
    error: Optional[str] = Field(None, description="Error message when status is failed")


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
