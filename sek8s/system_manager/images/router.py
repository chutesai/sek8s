"""Images submodule: FastAPI router for image management endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from sek8s.services.util import authorize

from .manager import ImageManager
from .models import PullRequest
from .responses import (
    ImageListEntry,
    ImageListResponse,
    PruneResponse,
    PullStartResponse,
    PullStartStatus,
    PullStatusEntry,
    PullStatusResponse,
)
from .util import resolve_to_full_ref

router = APIRouter()


def get_image_manager(request: Request) -> ImageManager:
    """FastAPI dependency that pulls the ImageManager off app.state."""
    return request.app.state.image_manager


@router.get(
    "/",
    response_model=ImageListResponse,
    summary="List images in containerd",
)
async def list_images(
    mgr: ImageManager = Depends(get_image_manager),
    _auth: bool = Depends(
        authorize(allow_miner=True, allow_validator=True, purpose="images")
    ),
) -> ImageListResponse:
    """List all images in containerd. Validator can access (read-only)."""
    entries = await mgr.list_images()
    return ImageListResponse(
        images=[
            ImageListEntry(ref=e.ref, digest=e.digest, size_bytes=e.size_bytes)
            for e in entries
        ]
    )


@router.post(
    "/pull",
    response_model=PullStartResponse,
    summary="Start image pull",
)
async def pull_image(
    request: PullRequest,
    mgr: ImageManager = Depends(get_image_manager),
    _auth: bool = Depends(
        authorize(allow_miner=True, allow_validator=False, purpose="images")
    ),
) -> PullStartResponse:
    """Start image pull (validator registry only, cosign-verified). Miner only."""
    status, _ = await mgr.start_pull(request.image)
    # Resolve to full ref for response (manager resolves short form internally)
    full_ref = resolve_to_full_ref(
        request.image, mgr.allowed_registries, mgr.default_org
    )
    if status == "present":
        return PullStartResponse(image_ref=full_ref, status=PullStartStatus.PRESENT)
    if status == "in_progress":
        return PullStartResponse(image_ref=full_ref, status=PullStartStatus.IN_PROGRESS)
    return PullStartResponse(image_ref=full_ref, status=PullStartStatus.STARTED)


@router.get(
    "/pull/status",
    response_model=PullStatusResponse,
    summary="Get pull status",
)
async def pull_status(
    image: Optional[str] = Query(
        None, description="Optional image to filter (short or full form)"
    ),
    mgr: ImageManager = Depends(get_image_manager),
    _auth: bool = Depends(
        authorize(allow_miner=True, allow_validator=False, purpose="images")
    ),
) -> PullStatusResponse:
    """Get pull status by image or all. Miner only."""
    snapshots = mgr.get_pull_status(image)
    return PullStatusResponse(
        pulls=[
            PullStatusEntry(
                image_ref=s.image_ref,
                status=s.status,
                error=s.error,
            )
            for s in snapshots
        ]
    )


@router.delete(
    "/{image:path}",
    summary="Remove image",
)
async def delete_image(
    image: str,
    force: bool = Query(False, description="Force delete even if in use"),
    mgr: ImageManager = Depends(get_image_manager),
    _auth: bool = Depends(
        authorize(allow_miner=True, allow_validator=False, purpose="images")
    ),
) -> dict:
    """Remove image by reference or ID. Miner only. Accepts short or full form."""
    await mgr.delete_image(image, force=force)
    return {"status": "ok", "message": "deleted"}


@router.post(
    "/prune",
    response_model=PruneResponse,
    summary="Prune unused images",
)
async def prune_images(
    mgr: ImageManager = Depends(get_image_manager),
    _auth: bool = Depends(
        authorize(allow_miner=True, allow_validator=False, purpose="images")
    ),
) -> PruneResponse:
    """Prune unused/dangling images. Miner only."""
    removed, freed = await mgr.prune()
    return PruneResponse(
        status="completed",
        removed_count=removed,
        freed_bytes=freed,
    )
