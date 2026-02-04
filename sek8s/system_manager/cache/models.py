"""Cache submodule: dataclasses and request/state models."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CacheChuteStatusEnum(str, Enum):
    """Status for a chute in the download status (GET) and overview."""

    IN_PROGRESS = "in_progress"
    PRESENT = "present"
    MISSING = "missing"
    FAILED = "failed"


class DownloadProgressStatus(str, Enum):
    """Progress phase for a chute download. Used internally; API exposes in_progress/present/missing."""

    STARTED = "started"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


_IN_PROGRESS_STATUSES = (
    DownloadProgressStatus.STARTED,
    DownloadProgressStatus.DOWNLOADING,
    DownloadProgressStatus.VERIFYING,
)


@dataclass
class ChuteDownloadState:
    """State for a single chute's in-progress or recently completed download."""

    chute_id: str
    status: DownloadProgressStatus
    repo_id: Optional[str] = None
    revision: Optional[str] = None
    error: Optional[str] = field(default=None, repr=False)
    # Only set when we know total size and bytes so far (e.g. from snapshot_download progress)
    bytes_downloaded: Optional[int] = None
    total_bytes: Optional[int] = None

    @property
    def is_in_progress(self) -> bool:
        return self.status in _IN_PROGRESS_STATUSES

    @property
    def percent_complete(self) -> Optional[float]:
        """Percent 0-100 only when both total_bytes and bytes_downloaded are known."""
        if self.total_bytes is not None and self.total_bytes > 0 and self.bytes_downloaded is not None:
            return min(100.0, max(0.0, 100.0 * self.bytes_downloaded / self.total_bytes))
        return None

    @property
    def api_status(self) -> CacheChuteStatusEnum:
        """Status for API response: in_progress, present, missing, or failed."""
        if self.status in _IN_PROGRESS_STATUSES:
            return CacheChuteStatusEnum.IN_PROGRESS
        if self.status == DownloadProgressStatus.FAILED:
            return CacheChuteStatusEnum.FAILED
        return CacheChuteStatusEnum.PRESENT


class DownloadStateManager:
    """Thread-safe manager for in-progress download state keyed by chute_id."""


    def __init__(self) -> None:
        self._state: dict[str, ChuteDownloadState] = {}
        self._lock = asyncio.Lock()

    async def get(self, chute_id: str) -> Optional[ChuteDownloadState]:
        async with self._lock:
            return self._state.get(chute_id)

    async def start(self, chute_id: str, repo_id: str, revision: str) -> None:
        async with self._lock:
            self._state[chute_id] = ChuteDownloadState(
                chute_id=chute_id,
                status=DownloadProgressStatus.STARTED,
                repo_id=repo_id,
                revision=revision,
            )

    async def set_downloading(self, chute_id: str) -> None:
        async with self._lock:
            if s := self._state.get(chute_id):
                s.status = DownloadProgressStatus.DOWNLOADING

    async def set_verifying(self, chute_id: str) -> None:
        async with self._lock:
            if s := self._state.get(chute_id):
                s.status = DownloadProgressStatus.VERIFYING

    async def set_progress(self, chute_id: str, bytes_downloaded: int, total_bytes: int) -> None:
        """Update progress when we know bytes downloaded and total (e.g. from HF progress callback)."""
        async with self._lock:
            if s := self._state.get(chute_id):
                s.bytes_downloaded = bytes_downloaded
                s.total_bytes = total_bytes

    async def set_completed(self, chute_id: str) -> None:
        async with self._lock:
            self._state.pop(chute_id, None)

    async def set_failed(self, chute_id: str, error: str) -> None:
        async with self._lock:
            if s := self._state.get(chute_id):
                s.status = DownloadProgressStatus.FAILED
                s.error = error

    async def contains(self, chute_id: str) -> bool:
        async with self._lock:
            return chute_id in self._state

    async def remove_if_completed(self, chute_id: str) -> None:
        async with self._lock:
            s = self._state.get(chute_id)
            if s is not None and s.status == DownloadProgressStatus.COMPLETED:
                self._state.pop(chute_id, None)

    async def all_entries(self) -> list[tuple[str, ChuteDownloadState]]:
        async with self._lock:
            return list(self._state.items())


# Module-level singleton for download state
download_state = DownloadStateManager()

class HfInfoResponse(BaseModel):
    """Response from validator hf_info endpoint (repo_id/revision for HF snapshot_download)."""

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
class CleanupResult:
    """Result of running cache cleanup (freed bytes and chute IDs removed)."""

    freed_bytes: int
    removed_chutes: list[str]
