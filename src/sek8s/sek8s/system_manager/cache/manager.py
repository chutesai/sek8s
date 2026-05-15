"""Cache submodule: HuggingFaceSnapshot and CacheManager business logic."""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Optional

from huggingface_hub import scan_cache_dir
from loguru import logger

from sek8s.config import cache_config

from .download import DownloadProcess
from .models import CacheChuteStatusEnum, ChuteSnapshot, CleanupResult
from .util import (
    CACHE_COMPLETE_MARKER,
    CACHE_STALE_MARKER,
    chmod_if_owned,
    fetch_hf_info,
    fetch_repo_total_size,
    verify_cache,
)


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
        self._download: Optional[DownloadProcess] = None
        self._reconciled: bool = False
        self._scan_cache: Optional[
            tuple[int, Optional[str], Optional[str], Optional[float]]
        ] = None
        self._scan_cache_at: float = 0.0

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
        return self._download is not None and self._download.is_running

    @property
    def needs_reconciliation(self) -> bool:
        """True when this entry should be (re-)verified against the validator."""
        return (
            self._download is None and not self._reconciled and self.is_present_on_disk
        )

    @property
    def status(self) -> CacheChuteStatusEnum:
        if self._download is not None:
            if self._download.is_running:
                return CacheChuteStatusEnum.IN_PROGRESS
            if self._download.is_done and not self._download.succeeded:
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
        if self._download is not None:
            return self._download.error
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
        if (
            not self.is_in_progress
            or self._download is None
            or self._download.total_bytes is None
            or self._download.total_bytes <= 0
        ):
            return None
        size = self.size_bytes
        if size is not None:
            return min(100.0, max(0.0, 100.0 * size / self._download.total_bytes))
        return None

    @property
    def download_rate(self) -> Optional[float]:
        """Average bytes/sec since this download session started."""
        if not self.is_in_progress or self._download is None:
            return None
        elapsed = time.monotonic() - self._download.started_at
        if elapsed <= 0:
            return None
        size = self.size_bytes
        if size is None:
            return None
        downloaded = size - self._download.initial_bytes
        if downloaded <= 0:
            return None
        return downloaded / elapsed

    @property
    def eta_seconds(self) -> Optional[float]:
        """Estimated seconds remaining based on current download rate."""
        rate = self.download_rate
        if (
            rate is None
            or rate <= 0
            or self._download is None
            or self._download.total_bytes is None
        ):
            return None
        remaining = self._download.total_bytes - (self.size_bytes or 0)
        if remaining <= 0:
            return 0.0
        return remaining / rate

    # ------------------------------------------------------------------
    # HF cache scanning
    # ------------------------------------------------------------------

    @property
    def _scan_ttl(self) -> float:
        """Shorter TTL while files are changing, longer once stable."""
        return 30.0 if self.is_in_progress else 60.0

    async def _du_size(self, path: Path) -> Optional[int]:
        """Get directory size in bytes via du -sb. Used for in-progress rate/ETA only.

        scan_cache_dir only sees files that have symlinks in the snapshot tree;
        HF creates symlinks when a file finishes downloading, so in-progress blobs
        are invisible to scan_cache_dir until each file completes.

        Runs du without sudo; cache dir is 2775 and system-manager is in group tdx.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "du",
                "-sb",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            if proc.returncode != 0:
                return None
            line = stdout.decode("utf-8", errors="replace").strip().split("\n")[0]
            return int(line.split()[0])
        except Exception:
            return None

    async def _scan_hub(
        self,
    ) -> tuple[int, Optional[str], Optional[str], Optional[float]]:
        """Scan HF cache directory off the event loop.

        Returns ``(size_bytes, repo_id, revision, last_accessed)``
        derived from a single ``scan_cache_dir`` call run in a thread.
        When in progress, size comes from ``du -sb`` because scan_cache_dir only
        sees completed files (symlinks are created when each file finishes).
        Results are cached per-instance; TTL varies by download status.
        """
        now = time.monotonic()
        if (
            self._scan_cache is not None
            and (now - self._scan_cache_at) < self._scan_ttl
        ):
            return self._scan_cache

        if not self.hub_path.exists():
            return (0, None, None, None)
        try:
            info = await asyncio.to_thread(scan_cache_dir, cache_dir=str(self.hub_path))
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
            if self.is_in_progress:
                du_size = await self._du_size(self.hub_path)
                if du_size is not None:
                    size = du_size
            result = (size, repo_id, revision, last_acc)
            self._scan_cache = result
            self._scan_cache_at = time.monotonic()
            return result
        except Exception:
            return (0, None, None, None)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    async def snapshot(self) -> ChuteSnapshot:
        """Point-in-time snapshot backed by a single ``scan_cache_dir`` call."""
        size, scan_repo_id, scan_revision, last_acc = await self._scan_hub()
        status = self.status
        pct = None
        rate = None
        eta = None
        dl = self._download
        if (
            self.is_in_progress
            and dl is not None
            and dl.total_bytes
            and dl.total_bytes > 0
            and size
        ):
            pct = min(100.0, max(0.0, 100.0 * size / dl.total_bytes))
            elapsed = time.monotonic() - dl.started_at
            downloaded = size - dl.initial_bytes
            if elapsed > 0 and downloaded > 0:
                rate = downloaded / elapsed
                remaining = dl.total_bytes - size
                eta = remaining / rate if remaining > 0 else 0.0
        return ChuteSnapshot(
            chute_id=self.chute_id,
            repo_id=self.repo_id or scan_repo_id or "",
            revision=self.revision or scan_revision,
            status=status,
            size_bytes=size,
            percent_complete=pct,
            download_rate=rate,
            eta_seconds=eta,
            last_accessed=last_acc,
            error=self.error,
        )

    # ------------------------------------------------------------------
    # Download lifecycle
    # ------------------------------------------------------------------

    async def start_download(self, repo_id: str, revision: str) -> None:
        """Prepare directories, create a DownloadProcess, and start it."""
        self.repo_id = repo_id
        self.revision = revision
        self._scan_cache = None

        self.path.mkdir(parents=True, exist_ok=True)
        chmod_if_owned(self.path, 0o2775)  # nosec B103
        self.hub_path.mkdir(exist_ok=True)
        chmod_if_owned(self.hub_path, 0o2775)  # nosec B103

        total_bytes: Optional[int] = None
        fetched = await fetch_repo_total_size(repo_id, revision)
        if fetched > 0:
            total_bytes = fetched

        initial_bytes = 0
        if self.hub_path.exists():
            try:
                info = await asyncio.to_thread(
                    scan_cache_dir, cache_dir=str(self.hub_path)
                )
                initial_bytes = info.size_on_disk
            except Exception as e:
                logger.debug(
                    "Could not read initial cache size for chute_id={}: {}",
                    self.chute_id,
                    e,
                )

        self._download = DownloadProcess(
            chute_id=self.chute_id,
            repo_id=repo_id,
            revision=revision,
            path=self.path,
            total_bytes=total_bytes,
            initial_bytes=initial_bytes,
        )
        task = asyncio.create_task(self._download.start())
        task.add_done_callback(lambda _: self.__dict__.update({"_scan_cache": None}))

    def cancel_download(self) -> None:
        if self._download is not None:
            self._download.cancel()

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
            logger.debug(
                "Reconcile {}: nothing on disk, marking reconciled", self.chute_id
            )
            return

        try:
            info = await fetch_hf_info(self.chute_id)
        except Exception as e:
            logger.warning(
                "Skipping reconciliation for {}: validator unavailable ({})",
                self.chute_id,
                e,
            )
            return

        repo_id = info.repo_id
        revision = info.revision or "main"
        if not repo_id:
            logger.warning(
                "Skipping reconciliation for {}: validator returned no repo_id",
                self.chute_id,
            )
            return

        logger.debug(
            "Reconcile {}: validator says repo={}, rev={} (was repo={}, rev={})",
            self.chute_id,
            repo_id,
            revision[:12],
            self.repo_id or "<unset>",
            (self.revision or "<unset>")[:12],
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
            self.chute_id,
            repo_id,
            revision[:12],
            self.path,
        )
        try:
            result = await verify_cache(
                repo_id=repo_id, revision=revision, cache_dir=str(self.path)
            )
            complete_marker.write_text(f"{repo_id}\n{revision}", encoding="utf-8")
            self._reconciled = True
            logger.info(
                "Reconciled {}: PRESENT (repo={}, rev={}, verified={}, skipped={})",
                self.chute_id,
                repo_id,
                revision[:12],
                result.get("verified", 0),
                result.get("skipped", 0),
            )
        except ValueError as e:
            error_msg = str(e)
            if "Missing file" in error_msg or "not found" in error_msg:
                logger.info(
                    "Reconciled {}: INCOMPLETE — repo={}, rev={}, reason={}",
                    self.chute_id,
                    repo_id,
                    revision[:12],
                    error_msg,
                )
            elif "verification failed" in error_msg:
                logger.warning(
                    "Reconciled {}: SKIPPED (could not fetch manifest) — repo={}, rev={}, reason={}",
                    self.chute_id,
                    repo_id,
                    revision[:12],
                    error_msg,
                )
            else:
                stale_marker.write_text(
                    f"{repo_id}\n{revision}\n{error_msg}", encoding="utf-8"
                )
                self._reconciled = True
                logger.warning(
                    "Reconciled {}: STALE — repo={}, rev={}, reason={}",
                    self.chute_id,
                    repo_id,
                    revision[:12],
                    error_msg,
                )
        except Exception as e:
            logger.warning(
                "Reconciliation error for {}: repo={}, rev={}, error={}",
                self.chute_id,
                repo_id,
                revision[:12],
                e,
            )

    async def purge_stale_revisions(self) -> int:
        """Remove HF cache revisions that don't match the current revision.

        Returns bytes freed.  Uses the HF hub ``delete_revisions`` API so
        only orphaned blobs (not referenced by any remaining snapshot) are
        removed.
        """
        if not self.hub_path.exists() or not self.revision:
            return 0
        try:
            info = await asyncio.to_thread(scan_cache_dir, cache_dir=str(self.hub_path))
        except Exception:
            return 0

        stale_hashes: list[str] = []
        for repo in info.repos:
            for rev in repo.revisions:
                if rev.commit_hash != self.revision:
                    stale_hashes.append(rev.commit_hash)

        if not stale_hashes:
            return 0

        try:
            strategy = info.delete_revisions(*stale_hashes)
            expected = strategy.expected_freed_size
            logger.info(
                "Purging {} stale revision(s) for chute_id={}, expected freed ~{}B",
                len(stale_hashes),
                self.chute_id,
                expected,
            )
            strategy.execute()
            return expected
        except Exception:
            logger.warning(
                "Failed to purge stale revisions for chute_id={}",
                self.chute_id,
                exc_info=True,
            )
            return 0

    async def delete(self) -> None:
        self.cancel_download()
        if not self.path.exists():
            return
        path = self.path.resolve()
        cache_base = Path(cache_config.cache_base).resolve()
        try:
            path.relative_to(cache_base)
        except ValueError:
            logger.error("Refusing to delete path outside cache_base: {}", path)
            raise PermissionError(
                f"Path {path} is not under cache_base {cache_base}"
            ) from None
        try:
            shutil.rmtree(path)
        except OSError as e:
            if e.errno != 1:  # EPERM
                raise
            logger.info(
                "rmtree failed (likely dir owned by pod 1000:1000), trying sudo rm for chute_id={}",
                self.chute_id,
            )
            proc = await asyncio.create_subprocess_exec(
                "sudo",
                "rm",
                "-rf",
                str(path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.error("sudo rm -rf failed for {}: {}", path, err_msg)
                raise OSError(
                    e.errno, f"Failed to delete cache dir: {err_msg}", str(path)
                ) from e


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
        self._last_sync: float = 0.0
        self._sync_cooldown: float = 5.0

    async def initialize(self) -> None:
        """Scan disk and reconcile all chute cache directories."""
        cache_base = Path(cache_config.cache_base).resolve()
        if not cache_base.exists():
            logger.info(
                "Cache base {} does not exist, skipping initialization", cache_base
            )
            return

        chute_dirs = [
            item
            for item in cache_base.iterdir()
            if item.is_dir() and len(item.name) == 36
        ]
        if not chute_dirs:
            logger.info("No chute cache directories found")
            return

        logger.info(
            "Initializing cache manager with {} directories...", len(chute_dirs)
        )
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
        downloaded by chute pods at runtime.  Skipped if called again
        within ``_sync_cooldown`` seconds of the last run.
        """
        now = time.monotonic()
        if now - self._last_sync < self._sync_cooldown:
            return
        self._last_sync = now

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
            new_snaps.append(
                HuggingFaceSnapshot(chute_id=item.name, externally_managed=True)
            )
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
        await self.sync_from_disk()
        async with self._lock:
            return self._chutes.get(chute_id)

    async def get_or_create(self, chute_id: str) -> HuggingFaceSnapshot:
        await self.sync_from_disk()
        async with self._lock:
            if chute_id not in self._chutes:
                self._chutes[chute_id] = HuggingFaceSnapshot(chute_id=chute_id)
            return self._chutes[chute_id]

    async def all(self) -> list[HuggingFaceSnapshot]:
        await self.sync_from_disk()
        async with self._lock:
            return list(self._chutes.values())

    async def all_snapshots(self) -> list[ChuteSnapshot]:
        """Return snapshots for all tracked chutes, scanning concurrently."""
        await self.sync_from_disk()
        async with self._lock:
            chutes = list(self._chutes.values())
        return list(await asyncio.gather(*(c.snapshot() for c in chutes)))

    async def remove(self, chute_id: str) -> bool:
        await self.sync_from_disk()
        async with self._lock:
            chute = self._chutes.pop(chute_id, None)
        if chute is not None:
            await chute.delete()
            return True
        return False

    async def cancel(self, chute_id: str, cleanup: bool = False) -> None:
        """Cancel an in-progress download.

        Raises:
            KeyError: if ``chute_id`` is not tracked.
            ValueError: if the download is not currently in progress.

        When ``cleanup`` is True, partial files are deleted after cancelling.
        """
        await self.sync_from_disk()
        async with self._lock:
            chute = self._chutes.get(chute_id)
        if chute is None:
            raise KeyError(chute_id)
        if not chute.is_in_progress:
            raise ValueError(f"No download in progress for chute_id={chute_id}")
        chute.cancel_download()
        logger.info("Cancelled download for chute_id={}, cleanup={}", chute_id, cleanup)
        if cleanup:
            async with self._lock:
                self._chutes.pop(chute_id, None)
            try:
                await chute.delete()
            except OSError as e:
                logger.warning(
                    "Failed to delete partial cache after cancel for chute_id={}: {}",
                    chute_id,
                    e,
                )

    async def cleanup(
        self,
        max_age_days: int,
        max_size_gb: int,
        exclude_pattern: Optional[str] = None,
    ) -> CleanupResult:
        """Remove cache entries by age and enforce max size; skip in-progress downloads."""
        await self.sync_from_disk()
        freed = 0
        removed_list: list[str] = []
        max_size_bytes = max_size_gb * 1024 * 1024 * 1024
        cutoff_time = time.time() - (max_age_days * 24 * 3600)

        async with self._lock:
            eligible = [c for c in self._chutes.values() if not c.is_in_progress]

        async def _scan(chute: HuggingFaceSnapshot):
            size, scan_repo_id, _, last_acc = await chute._scan_hub()
            return (chute, size, scan_repo_id, last_acc)

        scanned = await asyncio.gather(*(_scan(c) for c in eligible))

        candidates: list[tuple[HuggingFaceSnapshot, int, float]] = []
        for chute, size, scan_repo_id, last_acc in scanned:
            if size == 0:
                continue
            if (
                exclude_pattern
                and scan_repo_id
                and exclude_pattern.lower() in scan_repo_id.lower()
            ):
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
        candidates = [
            (c, s, la) for c, s, la in candidates if c.chute_id not in removed_set
        ]

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

        removed_set = set(removed_list)
        surviving = [c for c, _, _ in candidates if c.chute_id not in removed_set]
        purged = 0
        for chute in surviving:
            purged += await chute.purge_stale_revisions()

        return CleanupResult(
            freed_bytes=freed, removed_chutes=removed_list, purged_bytes=purged
        )

    async def purge_stale(self) -> int:
        """Purge stale HF revisions from all tracked chutes.

        Calls ``purge_stale_revisions()`` on every non-in-progress chute and
        returns the total bytes freed.  Does not remove any chutes.
        """
        await self.sync_from_disk()
        async with self._lock:
            chutes = [c for c in self._chutes.values() if not c.is_in_progress]

        results = await asyncio.gather(*(c.purge_stale_revisions() for c in chutes))
        total = sum(results)
        logger.info(
            "Purged stale HF revisions: {}B freed across {} chutes", total, len(chutes)
        )
        return total
