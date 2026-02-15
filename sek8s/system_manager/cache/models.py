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
    INCOMPLETE = "incomplete"  # Cache on disk but no completion marker and no active download


class DownloadPhase(str, Enum):
    """Progress phase for a chute download. Used internally; final status derived from task."""

    STARTED = "started"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"


_IN_PROGRESS_PHASES = (
    DownloadPhase.STARTED,
    DownloadPhase.DOWNLOADING,
    DownloadPhase.VERIFYING,
)


@dataclass
class ChuteDownloadState:
    """State for a single chute's in-progress or recently completed download."""

    chute_id: str
    phase: DownloadPhase
    repo_id: Optional[str] = None
    revision: Optional[str] = None
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    bytes_downloaded: Optional[int] = None
    total_bytes: Optional[int] = None

    @property
    def is_in_progress(self) -> bool:
        if self.task is not None:
            return not self.task.done()
        return self.phase in _IN_PROGRESS_PHASES

    @property
    def error(self) -> Optional[str]:
        """Return error string from task exception, or None."""
        if self.task is not None and self.task.done():
            try:
                exc = self.task.exception()
                if exc is not None:
                    return str(exc)
            except asyncio.CancelledError:
                return "Download was cancelled"
        return None

    @property
    def is_cache_complete(self) -> bool:
        from .util import is_cache_complete

        return is_cache_complete(self.chute_id)

    @property
    def percent_complete(self) -> Optional[float]:
        """Percent 0-100 from total_bytes and bytes_downloaded (or size on disk when in progress and not yet refreshed)."""
        if self.total_bytes is None or self.total_bytes <= 0:
            return None
        if self.bytes_downloaded is not None:
            return min(100.0, max(0.0, 100.0 * self.bytes_downloaded / self.total_bytes))
        if self.is_in_progress:
            from .util import chute_cache_size_on_disk

            size_on_disk = chute_cache_size_on_disk(self.chute_id)
            if size_on_disk is not None:
                return min(100.0, max(0.0, 100.0 * size_on_disk / self.total_bytes))
        return None

    @property
    def api_status(self) -> CacheChuteStatusEnum:
        """Status for API response — derived from task state when available, else from phase."""
        if self.task is not None:
            if not self.task.done():
                return CacheChuteStatusEnum.IN_PROGRESS
            try:
                if self.task.exception() is not None:
                    return CacheChuteStatusEnum.FAILED
            except asyncio.CancelledError:
                return CacheChuteStatusEnum.FAILED
            return CacheChuteStatusEnum.PRESENT
        # Fallback: before task is assigned, phase is always in-progress
        return CacheChuteStatusEnum.IN_PROGRESS


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
                phase=DownloadPhase.STARTED,
                repo_id=repo_id,
                revision=revision,
            )

    async def set_downloading(self, chute_id: str) -> None:
        async with self._lock:
            if s := self._state.get(chute_id):
                s.phase = DownloadPhase.DOWNLOADING

    async def set_verifying(self, chute_id: str) -> None:
        async with self._lock:
            if s := self._state.get(chute_id):
                s.phase = DownloadPhase.VERIFYING

    async def set_progress(self, chute_id: str, bytes_downloaded: int, total_bytes: int) -> None:
        """Update progress when we know bytes downloaded and total."""
        async with self._lock:
            if s := self._state.get(chute_id):
                s.bytes_downloaded = bytes_downloaded
                s.total_bytes = total_bytes

    async def set_task(self, chute_id: str, task: asyncio.Task) -> None:
        """Store the asyncio.Task on the state and suppress 'exception never retrieved' warning."""
        async with self._lock:
            if s := self._state.get(chute_id):
                s.task = task
                task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    async def refresh_in_progress_from_disk(self) -> None:
        """Update bytes_downloaded from disk for all in-progress downloads. Call when serving status."""
        from .util import chute_cache_size_on_disk

        async with self._lock:
            to_refresh = [
                (cid, s)
                for cid, s in self._state.items()
                if s.is_in_progress and s.total_bytes is not None and s.total_bytes > 0
            ]
        sizes = [(cid, chute_cache_size_on_disk(cid)) for cid, _ in to_refresh]
        async with self._lock:
            for (cid, _), (_, size_on_disk) in zip(to_refresh, sizes):
                if size_on_disk is not None and (s := self._state.get(cid)):
                    s.bytes_downloaded = size_on_disk

    async def set_completed(self, chute_id: str) -> None:
        async with self._lock:
            self._state.pop(chute_id, None)

    async def remove(self, chute_id: str) -> None:
        """Pop state and cancel the task if still running."""
        async with self._lock:
            s = self._state.pop(chute_id, None)
        if s is not None and s.task is not None and not s.task.done():
            s.task.cancel()

    async def contains(self, chute_id: str) -> bool:
        async with self._lock:
            return chute_id in self._state

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
