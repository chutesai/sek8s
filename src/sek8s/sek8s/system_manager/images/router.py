"""Images submodule: FastAPI router for image management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from sek8s.services.util import authorize

from .manager import ImageManager
from .responses import ImageListEntry, ImageListResponse, PruneResponse

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
