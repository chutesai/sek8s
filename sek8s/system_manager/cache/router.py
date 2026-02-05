"""Cache submodule: FastAPI router and route handlers."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from huggingface_hub import scan_cache_dir

from sek8s.config import cache_config
from sek8s.services.util import authorize

from .models import CacheChuteStatusEnum, CleanupRequest, DownloadRequest, download_state
from .responses import (
    CacheChuteStatus,
    CacheCleanupResponse,
    CacheDownloadResponse,
    CacheDownloadStatus,
    CacheDownloadStatusResponse,
    CacheOverviewEntry,
    CacheOverviewResponse,
)
from .util import (
    chute_cache_dir,
    fetch_hf_info,
    is_chute_present,
    run_cleanup,
    run_download,
)

router = APIRouter()


@router.post(
    "/download",
    response_model=CacheDownloadResponse,
    summary="Start download for a chute",
)
async def download(
    request: DownloadRequest,
    force: bool = Query(False, description="Re-download if already present"),
    _auth: bool = Depends(authorize(allow_miner=True, purpose="cache")),
) -> CacheDownloadResponse:
    chute_id = request.chute_id
    if not chute_id or len(chute_id) != 36:
        raise HTTPException(status_code=400, detail="chute_id must be a 36-char UUID")

    response_status: Optional[CacheDownloadStatus] = None
    s = await download_state.get(chute_id)
    if s is not None and s.is_in_progress:
        response_status = CacheDownloadStatus.IN_PROGRESS
    elif is_chute_present(chute_id) and not force:
        response_status = CacheDownloadStatus.PRESENT

    if response_status is None:
        try:
            info = await fetch_hf_info(chute_id)
        except HTTPException:
            raise
        repo_id = info.repo_id
        if not repo_id:
            raise HTTPException(status_code=502, detail="Validator did not return repo_id")
        revision = info.revision or "main"
        asyncio.create_task(
            run_download(chute_id, repo_id, revision)
        )
        response_status = CacheDownloadStatus.STARTED

    return CacheDownloadResponse(chute_id=chute_id, status=response_status)


def _chute_status_from_api_status(api_status: str) -> CacheChuteStatusEnum:
    """Map ChuteDownloadState.api_status string to API enum."""
    return CacheChuteStatusEnum(api_status)


@router.get(
    "/download/status",
    response_model=CacheDownloadStatusResponse,
    summary="Get download status by chute_id or all",
)
async def download_status(
    chute_id: Optional[str] = Query(None, description="Optional chute_id to filter"),
    _auth: bool = Depends(authorize(allow_miner=True, purpose="cache")),
) -> CacheDownloadStatusResponse:
    cache_base = Path(cache_config.cache_base).resolve()
    result: List[CacheChuteStatus] = []

    if chute_id:
        s = await download_state.get(chute_id)
        if s is not None:
            result.append(
                CacheChuteStatus(
                    chute_id=chute_id,
                    status=s.api_status,
                    percent_complete=s.percent_complete,
                    repo_id=s.repo_id,
                    revision=s.revision,
                    error=s.error,
                )
            )
        elif is_chute_present(chute_id):
            hub = cache_base / chute_id / "hub"
            try:
                info = scan_cache_dir(cache_dir=str(hub))
                size = info.size_on_disk
                repos = list(info.repos)
                repo_id = repos[0].repo_id if repos else ""
                revision = repos[0].refs[0].revision if repos and repos[0].refs else None
            except Exception:
                size = 0
                repo_id = ""
                revision = None
            result.append(
                CacheChuteStatus(
                    chute_id=chute_id,
                    status=CacheChuteStatusEnum.PRESENT,
                    repo_id=repo_id or None,
                    revision=revision,
                    size_bytes=size,
                )
            )
        else:
            result.append(
                CacheChuteStatus(chute_id=chute_id, status=CacheChuteStatusEnum.MISSING)
            )
    else:
        seen: set = set()
        for cid, s in await download_state.all_entries():
            seen.add(cid)
            result.append(
                CacheChuteStatus(
                    chute_id=cid,
                    status=s.api_status,
                    percent_complete=s.percent_complete,
                    repo_id=s.repo_id,
                    revision=s.revision,
                    size_bytes=None,
                    error=s.error,
                )
            )
        if cache_base.exists():
            for item in cache_base.iterdir():
                if item.is_dir() and len(item.name) == 36 and item.name not in seen:
                    hub = item / "hub"
                    if hub.exists() and any(hub.glob("models--*")):
                        try:
                            info = scan_cache_dir(cache_dir=str(hub))
                            repos = list(info.repos)
                            repo_id = repos[0].repo_id if repos else ""
                            revision = (
                                repos[0].refs[0].revision
                                if repos and repos[0].refs
                                else None
                            )
                            result.append(
                                CacheChuteStatus(
                                    chute_id=item.name,
                                    status=CacheChuteStatusEnum.PRESENT,
                                    repo_id=repo_id or None,
                                    revision=revision,
                                    size_bytes=getattr(info, "size_on_disk", None),
                                )
                            )
                            seen.add(item.name)
                        except Exception:
                            pass

    return CacheDownloadStatusResponse(chutes=result)


@router.delete(
    "/{chute_id}",
    summary="Remove cache for a chute",
)
async def delete_chute(
    chute_id: str,
    _auth: bool = Depends(authorize(allow_miner=True, purpose="cache")),
) -> dict:
    if len(chute_id) != 36:
        raise HTTPException(status_code=400, detail="chute_id must be a 36-char UUID")
    path = chute_cache_dir(chute_id)
    message = "deleted"
    if path.exists():
        if await download_state.contains(chute_id):
            raise HTTPException(
                status_code=409, detail="Download in progress for this chute"
            )
        shutil.rmtree(path, ignore_errors=False)
    else:
        message = "not found"
    return {"status": "ok", "message": message}


@router.post(
    "/cleanup",
    response_model=CacheCleanupResponse,
    summary="Cleanup cache by age and max size",
)
async def cleanup(
    body: Optional[CleanupRequest] = None,
    max_age_days: int = Query(5, ge=0),
    max_size_gb: int = Query(100, ge=0),
    _auth: bool = Depends(authorize(allow_miner=True, purpose="cache")),
) -> CacheCleanupResponse:
    max_age_days = body.max_age_days if body else max_age_days
    max_size_gb = body.max_size_gb if body else max_size_gb
    exclude_pattern = (body.exclude_pattern if body else None) or os.environ.get(
        "CLEANUP_EXCLUDE"
    )
    result = await run_cleanup(
        max_age_days, max_size_gb, exclude_pattern
    )
    return CacheCleanupResponse(
        status="completed",
        freed_bytes=result.freed_bytes,
        removed_chutes=result.removed_chutes,
    )


@router.get(
    "/overview",
    response_model=CacheOverviewResponse,
    summary="List cache contents and sizes",
)
async def overview(
    _auth: bool = Depends(authorize(allow_miner=True, purpose="cache")),
) -> CacheOverviewResponse:
    cache_base = Path(cache_config.cache_base).resolve()
    entries: List[CacheOverviewEntry] = []
    total = 0
    if cache_base.exists():
        for item in cache_base.iterdir():
            if not item.is_dir() or len(item.name) != 36:
                continue
            hub = item / "hub"
            if not hub.exists() or not list(hub.glob("models--*")):
                continue
            try:
                info = scan_cache_dir(cache_dir=str(hub))
                size = info.size_on_disk
                total += size
                repos = list(info.repos)
                repo_id = repos[0].repo_id if repos else ""
                revision = (
                    repos[0].refs[0].revision if repos and repos[0].refs else None
                )
                last_acc = max((r.last_accessed for r in info.repos), default=0)
            except Exception:
                repo_id = ""
                revision = None
                size = 0
                last_acc = None
            entries.append(
                CacheOverviewEntry(
                    chute_id=item.name,
                    repo_id=repo_id,
                    revision=revision,
                    size_bytes=size,
                    last_accessed=last_acc,
                )
            )
    return CacheOverviewResponse(total_size_bytes=total, chutes=entries)
