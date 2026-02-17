"""Cache submodule: FastAPI router and route handlers.

Every endpoint delegates to CacheManager / ChuteModel via
``request.app.state.cache_manager``.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from sek8s.services.util import authorize

from .manager import CacheManager
from .models import CacheChuteStatusEnum, CleanupRequest, ChuteSnapshot, DownloadRequest
from .responses import (
    CacheChuteStatus,
    CacheCleanupResponse,
    CacheDownloadResponse,
    CacheDownloadStatus,
    CacheDownloadStatusResponse,
    CacheOverviewEntry,
    CacheOverviewResponse,
)
from .util import fetch_hf_info

router = APIRouter()


def _snap_to_status(snap: ChuteSnapshot) -> CacheChuteStatus:
    return CacheChuteStatus(
        chute_id=snap.chute_id,
        status=snap.status,
        percent_complete=snap.percent_complete,
        repo_id=snap.repo_id or None,
        revision=snap.revision,
        size_bytes=snap.size_bytes or None,
        error=snap.error,
    )


def _snap_to_overview(snap: ChuteSnapshot) -> CacheOverviewEntry:
    return CacheOverviewEntry(
        chute_id=snap.chute_id,
        repo_id=snap.repo_id,
        revision=snap.revision,
        size_bytes=snap.size_bytes,
        last_accessed=snap.last_accessed,
        status=snap.status,
    )


async def get_cache_manager(request: Request) -> CacheManager:
    """FastAPI dependency that pulls the CacheManager off app.state."""
    return request.app.state.cache_manager


@router.post(
    "/download",
    response_model=CacheDownloadResponse,
    summary="Start download for a chute",
)
async def download(
    request: DownloadRequest,
    force: bool = Query(False, description="Re-download if already present"),
    mgr: CacheManager = Depends(get_cache_manager),
    _auth: bool = Depends(authorize(allow_miner=True, purpose="cache")),
) -> CacheDownloadResponse:
    chute_id = request.chute_id
    if not chute_id or len(chute_id) != 36:
        raise HTTPException(status_code=400, detail="chute_id must be a 36-char UUID")

    await mgr.sync_from_disk()
    chute = await mgr.get_or_create(chute_id)

    if chute.is_in_progress:
        return CacheDownloadResponse(chute_id=chute_id, status=CacheDownloadStatus.IN_PROGRESS)

    if chute.status == CacheChuteStatusEnum.PRESENT and not force:
        return CacheDownloadResponse(chute_id=chute_id, status=CacheDownloadStatus.PRESENT)

    try:
        info = await fetch_hf_info(chute_id)
    except HTTPException:
        raise
    repo_id = info.repo_id
    if not repo_id:
        raise HTTPException(status_code=502, detail="Validator did not return repo_id")
    revision = info.revision or "main"

    await chute.start_download(repo_id, revision)
    return CacheDownloadResponse(chute_id=chute_id, status=CacheDownloadStatus.STARTED)


@router.get(
    "/download/status",
    response_model=CacheDownloadStatusResponse,
    summary="Get download status by chute_id or all",
)
async def download_status(
    chute_id: Optional[str] = Query(None, description="Optional chute_id to filter"),
    mgr: CacheManager = Depends(get_cache_manager),
    _auth: bool = Depends(authorize(allow_miner=True, purpose="cache")),
) -> CacheDownloadStatusResponse:
    await mgr.sync_from_disk()
    if chute_id:
        chute = await mgr.get(chute_id)
        if chute is None:
            return CacheDownloadStatusResponse(
                chutes=[CacheChuteStatus(chute_id=chute_id, status=CacheChuteStatusEnum.MISSING)]
            )
        return CacheDownloadStatusResponse(chutes=[_snap_to_status(chute.snapshot())])

    chutes = await mgr.all()
    return CacheDownloadStatusResponse(chutes=[_snap_to_status(c.snapshot()) for c in chutes])


@router.delete(
    "/{chute_id}",
    summary="Remove cache for a chute",
)
async def delete_chute(
    chute_id: str,
    force: bool = Query(False, description="Force delete even if download is in progress"),
    mgr: CacheManager = Depends(get_cache_manager),
    _auth: bool = Depends(authorize(allow_miner=True, purpose="cache")),
) -> dict:
    if len(chute_id) != 36:
        raise HTTPException(status_code=400, detail="chute_id must be a 36-char UUID")

    await mgr.sync_from_disk()
    chute = await mgr.get(chute_id)
    if chute is None:
        return {"status": "ok", "message": "not found"}
    if chute.is_in_progress and not force:
        raise HTTPException(status_code=409, detail="Download in progress for this chute")

    await mgr.remove(chute_id)
    return {"status": "ok", "message": "deleted"}


@router.post(
    "/cleanup",
    response_model=CacheCleanupResponse,
    summary="Cleanup cache by age and max size",
)
async def cleanup(
    body: Optional[CleanupRequest] = None,
    max_age_days: int = Query(5, ge=0),
    max_size_gb: int = Query(100, ge=0),
    mgr: CacheManager = Depends(get_cache_manager),
    _auth: bool = Depends(authorize(allow_miner=True, purpose="cache")),
) -> CacheCleanupResponse:
    await mgr.sync_from_disk()
    age = body.max_age_days if body else max_age_days
    size = body.max_size_gb if body else max_size_gb
    exclude = (body.exclude_pattern if body else None) or os.environ.get("CLEANUP_EXCLUDE")

    result = await mgr.cleanup(age, size, exclude)
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
    mgr: CacheManager = Depends(get_cache_manager),
    _auth: bool = Depends(authorize(allow_miner=True, purpose="cache")),
) -> CacheOverviewResponse:
    await mgr.sync_from_disk()
    chutes = await mgr.all()
    entries = [_snap_to_overview(c.snapshot()) for c in chutes]
    total = sum(e.size_bytes for e in entries)
    return CacheOverviewResponse(total_size_bytes=total, chutes=entries)
