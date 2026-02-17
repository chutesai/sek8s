"""Cache submodule: pure data types (enums, request/response models, dataclasses)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CacheChuteStatusEnum(str, Enum):
    """Status for a chute in the download status (GET) and overview."""

    IN_PROGRESS = "in_progress"
    PRESENT = "present"
    MISSING = "missing"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    STALE = "stale"


class HfInfoResponse(BaseModel):
    """Response from validator hf_info endpoint."""

    repo_id: Optional[str] = Field(None, description="Hugging Face repo ID")
    revision: Optional[str] = Field(None, description="Repo revision; default 'main' if omitted")

    model_config = {"extra": "ignore"}


class DownloadRequest(BaseModel):
    chute_id: str = Field(..., description="Chute ID to download model for")


class CleanupRequest(BaseModel):
    max_age_days: int = Field(5, ge=0, description="Remove entries older than this many days")
    max_size_gb: int = Field(100, ge=0, description="Target max cache size in GB")
    exclude_pattern: Optional[str] = Field(None, description="Skip repos containing this string")


@dataclass
class ChuteSnapshot:
    """Point-in-time read of a HuggingFaceSnapshot's state (one scan_cache_dir call)."""

    chute_id: str
    repo_id: str
    revision: Optional[str]
    status: CacheChuteStatusEnum
    size_bytes: int
    percent_complete: Optional[float]
    last_accessed: Optional[float]
    error: Optional[str]


@dataclass
class CleanupResult:
    """Result of running cache cleanup."""

    freed_bytes: int
    removed_chutes: list[str]
