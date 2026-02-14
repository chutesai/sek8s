"""Cache submodule: helper functions for validator, verification, paths."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import List, Optional

import aiohttp
from huggingface_hub import scan_cache_dir, snapshot_download
from loguru import logger

from sek8s.config import cache_config
from sek8s.services.util import sign_request

from .models import CleanupResult, HfInfoResponse, download_state

# Marker file written after a successful download + verify; absence means incomplete/interrupted.
CACHE_COMPLETE_MARKER = ".cache_complete"

# In-memory cache for /misc/hf_repo_info responses keyed by (repo_id, revision).
_repo_info_cache: dict[tuple[str, str], dict] = {}
_repo_info_cache_lock = asyncio.Lock()


async def fetch_repo_info(repo_id: str, revision: str) -> Optional[dict]:
    """Fetch repo file list from validator /misc/hf_repo_info. Result is cached per (repo_id, revision)."""
    rev = revision or "main"
    key = (repo_id, rev)
    async with _repo_info_cache_lock:
        if key in _repo_info_cache:
            return _repo_info_cache[key]
        params = {"repo_id": repo_id, "repo_type": "model", "revision": rev}
        hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        if hf_token:
            params["hf_token"] = hf_token
        base = (cache_config.validator_base_url or "").strip().rstrip("/")
        repo_info_url = f"{base}/misc/hf_repo_info"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    repo_info_url, params=params, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        return None
                    repo_info = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None
        _repo_info_cache[key] = repo_info
        return repo_info


def chute_cache_dir(chute_id: str) -> Path:
    return Path(cache_config.cache_base).resolve() / chute_id


def is_chute_present(chute_id: str) -> bool:
    hub = chute_cache_dir(chute_id) / "hub"
    if not hub.exists():
        return False
    return any(hub.glob("models--*"))


def is_cache_complete(chute_id: str) -> bool:
    """True if cache exists and has the completion marker (successful download + verify)."""
    if not is_chute_present(chute_id):
        return False
    return (chute_cache_dir(chute_id) / CACHE_COMPLETE_MARKER).exists()


async def fetch_repo_total_size(repo_id: str, revision: str) -> int:
    """Return total byte size of repo from validator hf_repo_info; 0 on error. Uses cached repo info when available."""
    repo_info = await fetch_repo_info(repo_id, revision)
    if not repo_info:
        return 0
    total = 0
    for item in repo_info.get("files", []):
        if item.get("path", "").startswith("_"):
            continue
        total += item.get("size") or 0
    return total


def chute_cache_size_on_disk(chute_id: str) -> Optional[int]:
    """Return size on disk for chute's hub cache, or None if not present/unreadable."""
    hub = chute_cache_dir(chute_id) / "hub"
    if not hub.exists():
        return None
    try:
        info = scan_cache_dir(cache_dir=str(hub))
        return getattr(info, "size_on_disk", None)
    except Exception:
        return None


def _chmod_tree_for_group_write(path: Path, mode: int) -> None:
    """Recursively chmod path and its contents so group (tdx 1000) can write; we own the files."""
    try:
        for p in path.rglob("*"):
            try:
                os.chmod(p, mode)
            except OSError:
                pass
        os.chmod(path, mode)
    except OSError:
        pass


async def fetch_hf_info(chute_id: str) -> HfInfoResponse:
    """GET validator /chutes/{chute_id}/hf_info and return parsed HfInfoResponse.
    Request is signed with miner credentials when MINER_SS58/MINER_SEED are set.
    """
    from fastapi import HTTPException

    base = (cache_config.validator_base_url or "").strip().rstrip("/")
    if not base:
        raise HTTPException(
            status_code=503,
            detail="Validator base URL not configured (VALIDATOR_BASE_URL)",
        )
    url = f"{base}/chutes/{chute_id}/hf_info"
    headers, _  = sign_request(purpose="cache")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise HTTPException(
                        status_code=502,
                        detail={"error": "validator_error", "status": resp.status, "body": text},
                    )
                data = await resp.json()
                return HfInfoResponse.model_validate(data)
    except aiohttp.ClientError as e:
        logger.warning("Validator request failed: {}", e)
        raise HTTPException(status_code=502, detail="Validator request failed") from e


def get_symlink_hash(file_path: Path) -> Optional[str]:
    if file_path.is_symlink():
        target = os.readlink(file_path)
        blob_name = Path(target).name
        if len(blob_name) == 64:
            return blob_name
    return None


async def verify_cache(
    repo_id: str,
    revision: str,
    cache_dir: str,
) -> dict:
    """Verify cached HF model files; raises on failure. Uses cached repo info when available."""
    cache_dir_path = Path(cache_dir)
    repo_info = await fetch_repo_info(repo_id, revision)
    if not repo_info:
        raise ValueError(
            "Cache verification failed: could not fetch repo info from validator (required to verify cache integrity)"
        )

    remote_files = {}
    for item in repo_info.get("files", []):
        if item.get("path", "").startswith("_"):
            continue
        if item.get("is_lfs"):
            remote_files[item["path"]] = (item.get("sha256"), item.get("size"))
        else:
            remote_files[item["path"]] = (item.get("blob_id"), item.get("size"))

    repo_folder_name = f"models--{repo_id.replace('/', '--')}"
    snapshot_dir = cache_dir_path / "hub" / repo_folder_name / "snapshots" / revision
    if not snapshot_dir.exists():
        raise ValueError(f"Cache directory not found: {snapshot_dir}")

    local_files = {}
    for path in snapshot_dir.rglob("*"):
        if path.is_file() or path.is_symlink():
            rel_path = str(path.relative_to(snapshot_dir))
            if not any(part.startswith("_") for part in Path(rel_path).parts):
                local_files[rel_path] = path

    verified = 0
    skipped = 0
    for remote_path, (remote_hash, remote_size) in remote_files.items():
        local_path = local_files.get(remote_path)
        if not local_path or (not local_path.exists() and not local_path.is_symlink()):
            raise ValueError(f"Missing file: {remote_path}")
        if remote_hash is None or len(str(remote_hash)) == 40:
            skipped += 1
            continue
        resolved = local_path.resolve()
        if remote_size is not None and resolved.stat().st_size != remote_size:
            raise ValueError(f"Size mismatch: {remote_path}")
        symlink_hash = get_symlink_hash(local_path)
        if symlink_hash and symlink_hash != remote_hash:
            raise ValueError(f"Hash mismatch: {remote_path}")
        verified += 1

    return {"verified": verified, "skipped": skipped, "total": len(remote_files), "skipped_api_error": False}


async def run_download(
    chute_id: str,
    repo_id: str,
    revision: str,
) -> None:
    """Run snapshot_download then verify; register with state manager and update on completion/failure.

    Uses the same directory layout as the chute pod: hostPath /var/snap/cache/{chute_id} is
    mounted at /cache in the pod with HF_HOME=/cache (so the hub lives at /cache/hub). We create
    chute_dir/hub and pass that as cache_dir so the library writes there; no HF_HOME mutation
    so concurrent downloads are safe. On failure we remove the chute cache dir to avoid leaving
    partial or corrupted weights.
    """
    # system-manager runs with primary group tdx (1000); dirs we create are 10150:1000. chmod 2775 so pod (1000:1000) can write.
    cache_dir_path = chute_cache_dir(chute_id)
    cache_dir_path.mkdir(parents=True, exist_ok=True)
    os.chmod(cache_dir_path, 0o2775)
    hub_dir = cache_dir_path / "hub"
    hub_dir.mkdir(exist_ok=True)
    os.chmod(hub_dir, 0o2775)
    hub_cache_dir = str(hub_dir)

    await download_state.remove_if_completed(chute_id)
    await download_state.start(chute_id, repo_id, revision)
    try:
        await download_state.set_downloading(chute_id)
        total_bytes = await fetch_repo_total_size(repo_id, revision)
        if total_bytes > 0:
            await download_state.set_progress(chute_id, 0, total_bytes)

        def do_download() -> str:
            return snapshot_download(
                repo_id=repo_id,
                revision=revision,
                cache_dir=hub_cache_dir,
                local_dir_use_symlinks=True,
            )

        await asyncio.to_thread(do_download)

        await download_state.set_verifying(chute_id)

        await verify_cache(
            repo_id=repo_id,
            revision=revision,
            cache_dir=str(cache_dir_path),
        )

        # Recursively chmod 2775 so pod (GID 1000) can write to all dirs/files created by snapshot_download
        _chmod_tree_for_group_write(cache_dir_path, 0o2775)

        # Marker so status/overview can distinguish completed vs interrupted downloads
        (cache_dir_path / CACHE_COMPLETE_MARKER).write_text(
            f"{repo_id}\n{revision or 'main'}", encoding="utf-8"
        )

        # Set bytes_downloaded to total so state reflects 100% before we remove it
        s = await download_state.get(chute_id)
        if s is not None and s.total_bytes is not None:
            await download_state.set_progress(chute_id, s.total_bytes, s.total_bytes)
        await download_state.set_completed(chute_id)
    except Exception as e:
        logger.exception("Download failed for chute_id={}", chute_id)
        await download_state.set_failed(chute_id, str(e))
        # Remove chute cache dir so we don't leave partial/corrupted downloads;
        try:
            if cache_dir_path.exists():
                shutil.rmtree(cache_dir_path)
                logger.info(f"Cleaned up cache dir for {chute_id=} after failure")
        except OSError as cleanup_err:
            logger.warning(
                f"Failed to clean up cache dir for {chute_id=} after failure: {cleanup_err}"
            )


async def run_cleanup(
    max_age_days: int,
    max_size_gb: int,
    exclude_pattern: Optional[str] = None,
) -> CleanupResult:
    """Remove cache entries by age and enforce max size; skip in-progress downloads."""
    cache_base = Path(cache_config.cache_base).resolve()
    freed = 0
    removed_list: List[str] = []
    if not cache_base.exists():
        return CleanupResult(freed_bytes=0, removed_chutes=removed_list)

    max_size_bytes = max_size_gb * 1024 * 1024 * 1024
    cutoff_time = time.time() - (max_age_days * 24 * 3600)
    candidates: List[tuple] = []
    for item in cache_base.iterdir():
        if not item.is_dir() or len(item.name) != 36:
            continue
        hub = item / "hub"
        if not hub.exists() or not list(hub.glob("models--*")):
            continue
        try:
            info = scan_cache_dir(cache_dir=str(hub))
            size = info.size_on_disk
            last_acc = max((r.last_accessed for r in info.repos), default=0)
        except Exception:
            continue
        if exclude_pattern and exclude_pattern.lower() in (
            getattr(info.repos[0], "repo_id", "") if info.repos else ""
        ).lower():
            continue
        candidates.append((item, size, last_acc))

    for path, size, last_acc in candidates:
        if last_acc < cutoff_time:
            if await download_state.contains(path.name):
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed_list.append(path.name)
            freed += size
    candidates = [(p, sz, la) for p, sz, la in candidates if p.name not in removed_list]

    total_now = sum(sz for _, sz, _ in candidates)
    if total_now > max_size_bytes:
        candidates.sort(key=lambda x: x[1], reverse=True)
        for path, size, _ in candidates:
            if total_now <= max_size_bytes:
                break
            if await download_state.contains(path.name):
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed_list.append(path.name)
            freed += size
            total_now -= size

    return CleanupResult(freed_bytes=freed, removed_chutes=removed_list)
