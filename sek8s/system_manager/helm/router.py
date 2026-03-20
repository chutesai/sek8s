"""Helm submodule: FastAPI router for helm chart upgrade endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from sek8s.services.util import authorize

from .manager import HelmManager
from .models import UpgradeRequest
from .responses import (
    ReleaseListEntry,
    ReleaseListResponse,
    ReleaseStatusResponse,
    UpgradeStartResponse,
    UpgradeStartStatus,
    UpgradeStatusResponse,
)

router = APIRouter()


def get_helm_manager(request: Request) -> HelmManager:
    """FastAPI dependency that pulls the HelmManager off app.state."""
    return request.app.state.helm_manager


@router.post(
    "/upgrade",
    response_model=UpgradeStartResponse,
    summary="Start helm upgrade",
    responses={
        200: {"description": "Upgrade started, in progress, or already up to date"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        502: {"description": "Helm helper failed"},
    },
)
async def start_upgrade(
    body: UpgradeRequest,
    mgr: HelmManager = Depends(get_helm_manager),
    _auth: bool = Depends(authorize(allow_miner=True, allow_validator=True, purpose="helm")),
) -> UpgradeStartResponse:
    """Start helm upgrade for a release. Miner or validator."""
    status = await mgr.start_upgrade(body.release, body.version)
    return UpgradeStartResponse(release=body.release, status=status)


@router.get(
    "/releases",
    response_model=ReleaseListResponse,
    summary="List helm releases",
    responses={
        200: {"description": "List of helm releases"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        502: {"description": "Helm list failed"},
    },
)
async def list_releases(
    mgr: HelmManager = Depends(get_helm_manager),
    _auth: bool = Depends(authorize(allow_miner=True, allow_validator=True, purpose="helm")),
) -> ReleaseListResponse:
    """List all helm releases. Miner or validator."""
    entries = await mgr.list_releases()
    return ReleaseListResponse(
        releases=[
            ReleaseListEntry(
                name=e.name,
                namespace=e.namespace,
                chart=e.chart,
                status=e.status,
                revision=e.revision,
                updated=e.updated,
                app_version=e.app_version,
            )
            for e in entries
        ]
    )


@router.get(
    "/releases/{name}/status",
    response_model=ReleaseStatusResponse,
    summary="Get release status",
    responses={
        200: {"description": "Release status details"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        502: {"description": "Helm status failed"},
    },
)
async def get_release_status(
    name: str,
    mgr: HelmManager = Depends(get_helm_manager),
    _auth: bool = Depends(authorize(allow_miner=True, allow_validator=True, purpose="helm")),
) -> ReleaseStatusResponse:
    """Get detailed status for a release. Miner or validator."""
    detail = await mgr.get_release_status(name)
    return ReleaseStatusResponse.from_detail(detail)


@router.get(
    "/upgrade/status",
    response_model=UpgradeStatusResponse,
    summary="Get upgrade operation status",
    responses={
        200: {"description": "Upgrade status (in progress, completed, or failed)"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "No upgrade in progress"},
    },
)
async def get_upgrade_status(
    mgr: HelmManager = Depends(get_helm_manager),
    _auth: bool = Depends(authorize(allow_miner=True, allow_validator=True, purpose="helm")),
) -> UpgradeStatusResponse:
    """Get async upgrade operation status. Miner or validator."""
    snapshot = mgr.get_upgrade_status()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No upgrade in progress")
    return UpgradeStatusResponse(
        release=snapshot.release,
        status=snapshot.status,
        error=snapshot.error,
    )
