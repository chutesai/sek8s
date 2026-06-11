"""Download module: DownloadProcess class and subprocess pipeline entry point.

``DownloadProcess`` manages the lifecycle of a single model download: it spawns
this same module as a subprocess (``-m sek8s.system_manager.cache.download``),
awaits completion, and exposes clean state properties to callers.

When invoked as a subprocess the full pipeline runs in order:

1. ``snapshot_download`` -- fetch all model files into ``<path>/hub/``
2. ``verify_cache``       -- validate files against the validator manifest
3. ``chmod_tree``         -- set group-writable permissions on owned files
4. Write ``<path>/.cache_complete`` marker
5. Remove ``<path>/.cache_stale`` marker if present

Exits 0 on success, 1 on error (full traceback written to stderr).
SIGTERM causes an immediate exit with a negative returncode (-15); no special
handling is needed here — the OS handles it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

from huggingface_hub import snapshot_download
from loguru import logger

from sek8s.system_manager.cache.util import (
    CACHE_COMPLETE_MARKER,
    CACHE_STALE_MARKER,
    chmod_tree,
    verify_cache,
)


class DownloadProcess:
    """Manages the lifecycle of a single model download subprocess.

    Constructed with everything needed to spawn the download, then started via
    ``start()``.  All state (running, done, error) is derived from the subprocess
    returncode and captured stderr -- there is no asyncio Task visible to callers.

    ``HuggingFaceSnapshot`` holds one instance while a download is active and
    delegates all download-state queries to it.  If the download mechanism ever
    changes (e.g. a different backend), only this class and the pipeline below
    need updating.
    """

    def __init__(
        self,
        chute_id: str,
        repo_id: str,
        revision: str,
        path: Path,
        total_bytes: Optional[int],
        initial_bytes: int,
    ):
        self.chute_id = chute_id
        self.repo_id = repo_id
        self.revision = revision
        self.path = path
        self.total_bytes = total_bytes
        self.initial_bytes = initial_bytes
        self.started_at: float = time.monotonic()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._returncode: Optional[int] = None
        self._error: Optional[str] = None

    # ------------------------------------------------------------------
    # State properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def is_done(self) -> bool:
        return self._returncode is not None

    @property
    def succeeded(self) -> bool:
        return self._returncode == 0

    @property
    def was_cancelled(self) -> bool:
        return self._returncode is not None and self._returncode < 0

    @property
    def error(self) -> Optional[str]:
        if not self.is_done:
            return None
        if self.was_cancelled:
            return "Download was cancelled"
        return self._error  # stderr output; None on success

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn the download subprocess and await completion.

        Stores returncode and stderr on exit.  Designed to be wrapped in
        ``asyncio.create_task`` by the caller so it runs concurrently.
        """
        try:
            self._proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "sek8s.system_manager.cache.download",
                "--repo-id",
                self.repo_id,
                "--revision",
                self.revision,
                "--path",
                str(self.path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr_bytes = await self._proc.communicate()
            self._returncode = self._proc.returncode
            self._proc = None
            if self._returncode != 0:
                self._error = (
                    (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
                )
                logger.error(
                    "Download subprocess failed for chute_id={} (rc={}): {}",
                    self.chute_id,
                    self._returncode,
                    self._error,
                )
            else:
                logger.info(
                    "Download subprocess completed for chute_id={}",
                    self.chute_id,
                )
        except asyncio.CancelledError:
            self.cancel()
            raise

    def cancel(self) -> None:
        """Send SIGTERM to the download subprocess if it is still running."""
        if self._proc is not None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
            self._proc = None


# ======================================================================
# Subprocess pipeline (entry point when run as -m)
# ======================================================================


_DOWNLOAD_MAX_RETRIES = 5
_DOWNLOAD_RETRY_BASE_DELAY = 10


def _snapshot_download_with_retry(repo_id: str, revision: str, cache_dir: str) -> None:
    """Call snapshot_download with retries for transient CDN failures.

    HuggingFace's CDN serves files via presigned URLs that expire after ~60
    minutes.  For large models the download can outlast the URL TTL, producing
    403 Forbidden errors from hf_transfer.  Retrying is safe because
    snapshot_download resumes from the HF cache -- already-complete files are
    skipped and partial blobs are re-fetched.
    """
    for attempt in range(1, _DOWNLOAD_MAX_RETRIES + 1):
        try:
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                cache_dir=cache_dir,
            )
            return
        except Exception as exc:
            exc_text = str(exc)
            is_transient = "403" in exc_text or "Forbidden" in exc_text
            if not is_transient or attempt == _DOWNLOAD_MAX_RETRIES:
                raise
            delay = _DOWNLOAD_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            print(
                f"[download] Transient failure (attempt {attempt}/"
                f"{_DOWNLOAD_MAX_RETRIES}), retrying in {delay}s: {exc_text}",
                file=sys.stderr,
            )
            time.sleep(delay)


async def _run(repo_id: str, revision: str, path: Path) -> None:
    hub_path = path / "hub"

    _snapshot_download_with_retry(
        repo_id=repo_id,
        revision=revision,
        cache_dir=str(hub_path),
    )

    await verify_cache(
        repo_id=repo_id,
        revision=revision,
        cache_dir=str(path),
    )

    chmod_tree(path, 0o2775)  # nosec B103

    (path / CACHE_COMPLETE_MARKER).write_text(
        f"{repo_id}\n{revision}", encoding="utf-8"
    )
    stale = path / CACHE_STALE_MARKER
    if stale.exists():
        stale.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and verify a HuggingFace model snapshot."
    )
    parser.add_argument("--repo-id", required=True, help="HuggingFace repo ID")
    parser.add_argument(
        "--revision", required=True, help="Revision (branch, tag, or commit hash)"
    )
    parser.add_argument(
        "--path",
        required=True,
        help="Chute cache directory (hub/ subdirectory used as HF cache root)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(args.repo_id, args.revision, Path(args.path)))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
