"""Cache submodule: HuggingFaceSnapshot and CacheManager business logic."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from huggingface_hub import scan_cache_dir, snapshot_download
from loguru import logger

from sek8s.config import cache_config

from .models import CacheChuteStatusEnum, ChuteSnapshot, CleanupResult
from .util import fetch_hf_info, fetch_repo_total_size, verify_cache

CACHE_COMPLETE_MARKER = ".cache_complete"
CACHE_STALE_MARKER = ".cache_stale"


class HuggingFaceSnapshot:
    """A HuggingFace model snapshot cached on disk for a specific chute.

    Each instance corresponds to one ``{cache_base}/{chute_id}`` directory.
    Status, size, progress, and download lifecycle are all derived from
    the in-memory task state and the on-disk marker files.
    """

    def __init__(
        self,
        chute_id: str,
        repo_id: str = "",
        revision: Optional[str] = None,
        *,
        externally_managed: bool = False,
    ):
        self.chute_id = chute_id
        self.repo_id = repo_id
        self.revision = revision
        self.externally_managed = externally_managed
        self._task: Optional[asyncio.Task] = None
        self._total_bytes: Optional[int] = None
        self._reconciled: bool = False

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return Path(cache_config.cache_base).resolve() / self.chute_id

    @property
    def hub_path(self) -> Path:
        return self.path / "hub"

    @property
    def is_present_on_disk(self) -> bool:
        return self.hub_path.exists() and any(self.hub_path.glob("models--*"))

    # ------------------------------------------------------------------
    # Status / progress
    # ------------------------------------------------------------------

    @property
    def is_in_progress(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def needs_reconciliation(self) -> bool:
        """True when this entry should be (re-)verified against the validator."""
        return not self._reconciled and self._task is None and self.is_present_on_disk

    @property
    def status(self) -> CacheChuteStatusEnum:
        if self._task is not None:
            if not self._task.done():
                return CacheChuteStatusEnum.IN_PROGRESS
            try:
                if self._task.exception() is not None:
                    return CacheChuteStatusEnum.FAILED
            except asyncio.CancelledError:
                return CacheChuteStatusEnum.FAILED
        if (self.path / CACHE_COMPLETE_MARKER).exists():
            return CacheChuteStatusEnum.PRESENT
        if (self.path / CACHE_STALE_MARKER).exists():
            return CacheChuteStatusEnum.STALE
        if self.is_present_on_disk:
            return CacheChuteStatusEnum.INCOMPLETE
        return CacheChuteStatusEnum.MISSING

    @property
    def error(self) -> Optional[str]:
        if self._task is not None and self._task.done():
            try:
                exc = self._task.exception()
                if exc is not None:
                    return str(exc)
            except asyncio.CancelledError:
                return "Download was cancelled"
        return None

    @property
    def size_bytes(self) -> Optional[int]:
        if not self.hub_path.exists():
            return None
        try:
            info = scan_cache_dir(cache_dir=str(self.hub_path))
            return info.size_on_disk
        except Exception:
            return None

    @property
    def percent_complete(self) -> Optional[float]:
        if not self.is_in_progress or self._total_bytes is None or self._total_bytes <= 0:
            return None
        size = self.size_bytes
        if size is not None:
            return min(100.0, max(0.0, 100.0 * size / self._total_bytes))
        return None

    # ------------------------------------------------------------------
    # HF cache scanning
    # ------------------------------------------------------------------

    def _scan_hub(self) -> tuple[int, Optional[str], Optional[str], Optional[float]]:
        """Scan HF cache directory.

        Returns ``(size_bytes, repo_id, revision, last_accessed)``
        derived from a single ``scan_cache_dir`` call.
        """
        if not self.hub_path.exists():
            return (0, None, None, None)
        try:
            info = scan_cache_dir(cache_dir=str(self.hub_path))
            size = info.size_on_disk
            repo_id: Optional[str] = None
            revision: Optional[str] = None
            last_acc: Optional[float] = None
            repos = list(info.repos)
            if repos:
                repo_id = repos[0].repo_id
                revisions = list(repos[0].revisions)
                if revisions:
                    revision = revisions[0].commit_hash
                last_acc = max((r.last_accessed for r in repos), default=None)
            return (size, repo_id, revision, last_acc)
        except Exception:
            return (0, None, None, None)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> ChuteSnapshot:
        """Point-in-time snapshot backed by a single ``scan_cache_dir`` call."""
        size, scan_repo_id, scan_revision, last_acc = self._scan_hub()
        status = self.status
        return ChuteSnapshot(
            chute_id=self.chute_id,
            repo_id=self.repo_id or scan_repo_id or "",
            revision=self.revision or scan_revision,
            status=status,
            size_bytes=size,
            percent_complete=self.percent_complete,
            last_accessed=last_acc,
            error=self.error,
        )

    # ------------------------------------------------------------------
    # Download lifecycle
    # ------------------------------------------------------------------

    async def start_download(self, repo_id: str, revision: str) -> None:
        """Prepare directories and launch the download task."""
        self.repo_id = repo_id
        self.revision = revision

        self.path.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path, 0o2775)
        self.hub_path.mkdir(exist_ok=True)
        os.chmod(self.hub_path, 0o2775)

        total_bytes = await fetch_repo_total_size(repo_id, revision)
        if total_bytes > 0:
            self._total_bytes = total_bytes

        self._task = asyncio.create_task(self._run_download())
        self._task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    @staticmethod
    def _chmod_tree(path: Path, mode: int) -> None:
        """Recursively chmod path and its contents so group can write."""
        try:
            for p in path.rglob("*"):
                try:
                    os.chmod(p, mode)
                except OSError:
                    pass
            os.chmod(path, mode)
        except OSError:
            pass

    async def _run_download(self) -> None:
        """Execute snapshot_download, verify, chmod, and write markers."""
        hub_cache_dir = str(self.hub_path)
        try:
            def do_download() -> str:
                return snapshot_download(
                    repo_id=self.repo_id,
                    revision=self.revision,
                    cache_dir=hub_cache_dir,
                    local_dir_use_symlinks=True,
                )

            await asyncio.to_thread(do_download)

            await verify_cache(
                repo_id=self.repo_id,
                revision=self.revision,
                cache_dir=str(self.path),
            )

            self._chmod_tree(self.path, 0o2775)

            (self.path / CACHE_COMPLETE_MARKER).write_text(
                f"{self.repo_id}\n{self.revision or 'main'}", encoding="utf-8"
            )
            stale_marker = self.path / CACHE_STALE_MARKER
            if stale_marker.exists():
                stale_marker.unlink()
        except Exception:
            logger.exception("Download failed for chute_id={}", self.chute_id)
            try:
                if self.path.exists():
                    shutil.rmtree(self.path)
                    logger.info("Cleaned up cache dir for chute_id={} after failure", self.chute_id)
            except OSError as cleanup_err:
                logger.warning(
                    "Failed to clean up cache dir for chute_id={}: {}", self.chute_id, cleanup_err
                )
            raise

    def cancel_download(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    # ------------------------------------------------------------------
    # Identity & reconciliation
    # ------------------------------------------------------------------

    async def fetch_identity(self) -> None:
        """Populate repo_id and revision from the validator without verifying files.

        Used during discovery so we know *what* this cache entry represents
        without paying the cost of a full verification (which would always
        fail for an in-progress download anyway).
        """
        try:
            info = await fetch_hf_info(self.chute_id)
        except Exception as e:
            logger.warning("Could not fetch identity for {}: {}", self.chute_id, e)
            return
        if info.repo_id:
            self.repo_id = info.repo_id
            self.revision = info.revision or "main"

    async def reconcile(self) -> None:
        """Verify cache against the validator's current revision and set markers.

        Outcome determines both disk markers and the ``_reconciled`` flag:

        * **PRESENT** — all files verified.  ``_reconciled = True``.
        * **STALE** — hash or size mismatch (wrong revision on disk).
          ``_reconciled = True`` (definitive).
        * **INCOMPLETE** — files are missing (download may still be in
          flight from another process).  ``_reconciled`` stays ``False``
          so the next :meth:`CacheManager.sync_from_disk` re-checks.
        * **Validator unreachable** — ``_reconciled`` stays ``False``.
        """
        if not self.is_present_on_disk:
            self._reconciled = True
            logger.debug("Reconcile {}: nothing on disk, marking reconciled", self.chute_id)
            return

        try:
            info = await fetch_hf_info(self.chute_id)
        except Exception as e:
            logger.warning("Skipping reconciliation for {}: validator unavailable ({})", self.chute_id, e)
            return

        repo_id = info.repo_id
        revision = info.revision or "main"
        if not repo_id:
            logger.warning("Skipping reconciliation for {}: validator returned no repo_id", self.chute_id)
            return

        logger.debug(
            "Reconcile {}: validator says repo={}, rev={} (was repo={}, rev={})",
            self.chute_id, repo_id, revision[:12],
            self.repo_id or "<unset>", (self.revision or "<unset>")[:12],
        )
        self.repo_id = repo_id
        self.revision = revision

        complete_marker = self.path / CACHE_COMPLETE_MARKER
        stale_marker = self.path / CACHE_STALE_MARKER

        # Clear any previous markers so we always start from a clean state.
        if complete_marker.exists():
            complete_marker.unlink()
        if stale_marker.exists():
            stale_marker.unlink()

        logger.info(
            "Verifying cache for {}: repo={}, rev={}, path={}",
            self.chute_id, repo_id, revision[:12], self.path,
        )
        try:
            result = await verify_cache(repo_id=repo_id, revision=revision, cache_dir=str(self.path))
            complete_marker.write_text(f"{repo_id}\n{revision}", encoding="utf-8")
            self._reconciled = True
            logger.info(
                "Reconciled {}: PRESENT (repo={}, rev={}, verified={}, skipped={})",
                self.chute_id, repo_id, revision[:12],
                result.get("verified", 0), result.get("skipped", 0),
            )
        except ValueError as e:
            error_msg = str(e)
            if "Missing file" in error_msg or "not found" in error_msg:
                logger.info(
                    "Reconciled {}: INCOMPLETE — repo={}, rev={}, reason={}",
                    self.chute_id, repo_id, revision[:12], error_msg,
                )
            elif "verification failed" in error_msg:
                logger.warning(
                    "Reconciled {}: SKIPPED (could not fetch manifest) — repo={}, rev={}, reason={}",
                    self.chute_id, repo_id, revision[:12], error_msg,
                )
            else:
                stale_marker.write_text(f"{repo_id}\n{revision}\n{error_msg}", encoding="utf-8")
                self._reconciled = True
                logger.warning(
                    "Reconciled {}: STALE — repo={}, rev={}, reason={}",
                    self.chute_id, repo_id, revision[:12], error_msg,
                )
        except Exception as e:
            logger.warning(
                "Reconciliation error for {}: repo={}, rev={}, error={}",
                self.chute_id, repo_id, revision[:12], e,
            )

    async def delete(self) -> None:
        self.cancel_download()
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)


# ======================================================================
# CacheManager
# ======================================================================


class CacheManager:
    """Manages all cached HuggingFace snapshots in memory.

    Created once during the application lifespan.  On startup, it scans the
    cache directory, creates a ``HuggingFaceSnapshot`` per UUID directory found,
    and runs reconciliation against the validator for each.
    """

    def __init__(self) -> None:
        self._chutes: dict[str, HuggingFaceSnapshot] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Scan disk and reconcile all chute cache directories."""
        cache_base = Path(cache_config.cache_base).resolve()
        if not cache_base.exists():
            logger.info("Cache base {} does not exist, skipping initialization", cache_base)
            return

        chute_dirs = [
            item for item in cache_base.iterdir()
            if item.is_dir() and len(item.name) == 36
        ]
        if not chute_dirs:
            logger.info("No chute cache directories found")
            return

        logger.info("Initializing cache manager with {} directories...", len(chute_dirs))
        for item in chute_dirs:
            hub = item / "hub"
            if not hub.exists() or not any(hub.glob("models--*")):
                continue
            chute = HuggingFaceSnapshot(chute_id=item.name)
            await chute.reconcile()
            self._chutes[item.name] = chute

        logger.info("Cache manager initialized: {} chutes loaded", len(self._chutes))

    async def sync_from_disk(self) -> None:
        """Discover new on-disk directories and reconcile pending entries.

        Called before query endpoints so the manager always reflects the
        current state of the shared cache volume — including models
        downloaded by chute pods at runtime.
        """
        cache_base = Path(cache_config.cache_base).resolve()
        if not cache_base.exists():
            return

        for snap in self._discover_new_entries(cache_base):
            await snap.fetch_identity()
            async with self._lock:
                if snap.chute_id not in self._chutes:
                    self._chutes[snap.chute_id] = snap

        await self._reconcile_pending()

    def _discover_new_entries(self, cache_base: Path) -> list[HuggingFaceSnapshot]:
        """Return new on-disk chute directories not yet tracked by the manager."""
        known_ids = set(self._chutes.keys())
        new_snaps: list[HuggingFaceSnapshot] = []
        for item in cache_base.iterdir():
            if not item.is_dir() or len(item.name) != 36:
                continue
            if item.name in known_ids:
                continue
            hub = item / "hub"
            if not hub.exists() or not any(hub.glob("models--*")):
                continue
            new_snaps.append(HuggingFaceSnapshot(chute_id=item.name, externally_managed=True))
        return new_snaps

    async def _reconcile_pending(self) -> None:
        """Re-reconcile tracked entries that still need verification.

        This catches externally-managed entries whose download may have
        completed since the last sync (missing files are now present).
        """
        pending: list[HuggingFaceSnapshot] = []
        async with self._lock:
            for snap in self._chutes.values():
                if snap.needs_reconciliation:
                    pending.append(snap)

        for snap in pending:
            await snap.reconcile()

    async def get(self, chute_id: str) -> Optional[HuggingFaceSnapshot]:
        async with self._lock:
            return self._chutes.get(chute_id)

    async def get_or_create(self, chute_id: str) -> HuggingFaceSnapshot:
        async with self._lock:
            if chute_id not in self._chutes:
                self._chutes[chute_id] = HuggingFaceSnapshot(chute_id=chute_id)
            return self._chutes[chute_id]

    async def all(self) -> list[HuggingFaceSnapshot]:
        async with self._lock:
            return list(self._chutes.values())

    async def remove(self, chute_id: str) -> bool:
        async with self._lock:
            chute = self._chutes.pop(chute_id, None)
        if chute is not None:
            await chute.delete()
            return True
        return False

    async def cleanup(
        self,
        max_age_days: int,
        max_size_gb: int,
        exclude_pattern: Optional[str] = None,
    ) -> CleanupResult:
        """Remove cache entries by age and enforce max size; skip in-progress downloads."""
        freed = 0
        removed_list: list[str] = []
        max_size_bytes = max_size_gb * 1024 * 1024 * 1024
        cutoff_time = time.time() - (max_age_days * 24 * 3600)

        candidates: list[tuple[HuggingFaceSnapshot, int, float]] = []
        async with self._lock:
            for chute in list(self._chutes.values()):
                if chute.is_in_progress:
                    continue
                size, scan_repo_id, _, last_acc = chute._scan_hub()
                if size == 0:
                    continue
                if exclude_pattern and scan_repo_id and exclude_pattern.lower() in scan_repo_id.lower():
                    continue
                candidates.append((chute, size, last_acc or 0))

        for chute, size, last_acc in candidates:
            if last_acc < cutoff_time:
                async with self._lock:
                    self._chutes.pop(chute.chute_id, None)
                await chute.delete()
                removed_list.append(chute.chute_id)
                freed += size

        removed_set = set(removed_list)
        candidates = [(c, s, la) for c, s, la in candidates if c.chute_id not in removed_set]

        total_now = sum(s for _, s, _ in candidates)
        if total_now > max_size_bytes:
            candidates.sort(key=lambda x: x[1], reverse=True)
            for chute, size, _ in candidates:
                if total_now <= max_size_bytes:
                    break
                async with self._lock:
                    self._chutes.pop(chute.chute_id, None)
                await chute.delete()
                removed_list.append(chute.chute_id)
                freed += size
                total_now -= size

        return CleanupResult(freed_bytes=freed, removed_chutes=removed_list)
