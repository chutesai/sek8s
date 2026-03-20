"""Helm submodule: HelmManager for k3s helm operations via k3s-helm-helper."""

from __future__ import annotations

import asyncio
import json
from typing import List, Optional, Tuple

from fastapi import HTTPException
from loguru import logger

from .models import (
    ReleaseEntry,
    ReleaseStatusDetail,
    UpgradeSnapshot,
    UpgradeStartStatus,
    UpgradeStatusEnum,
)

K3S_HELM_HELPER = "/usr/local/bin/k3s-helm-helper"
UPGRADE_TIMEOUT = 600.0  # 10 minutes


class HelmManager:
    """Manages helm releases: list, status, upgrade (async)."""

    def __init__(self, upgrade_timeout: float = UPGRADE_TIMEOUT):
        self.upgrade_timeout = upgrade_timeout
        self._upgrade_task: Optional[asyncio.Task] = None
        self._upgrade_result: Optional[Tuple[UpgradeStatusEnum, Optional[str]]] = None
        self._upgrade_release: Optional[str] = None

    async def _run(
        self,
        subcommand: str,
        *args: str,
        timeout: Optional[float] = None,
    ) -> Tuple[int, str, str]:
        """Run k3s-helm-helper via sudo. Returns (exit_code, stdout, stderr)."""
        cmd = ["sudo", K3S_HELM_HELPER, subcommand, *args]
        to = timeout or 30.0
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=to
        )
        return (
            process.returncode or 0,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )

    async def list_releases(self) -> List[ReleaseEntry]:
        """List all helm releases via k3s-helm-helper list."""
        code, stdout, stderr = await self._run("list", timeout=30.0)
        if code != 0:
            logger.error("k3s-helm-helper list failed: {}", stderr)
            raise HTTPException(
                status_code=502,
                detail={"error": "helm_list_failed", "stderr": stderr},
            )
        try:
            return ReleaseEntry.from_list_json(stdout)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse helm list output: {}", e)
            raise HTTPException(
                status_code=502,
                detail={"error": "helm_list_parse_failed", "message": str(e)},
            ) from e
        except ValueError as e:
            logger.error("Invalid helm list format: {}", e)
            raise HTTPException(
                status_code=502,
                detail={"error": "helm_list_unexpected_format", "message": str(e)},
            ) from e

    async def get_release_status(self, name: str) -> ReleaseStatusDetail:
        """Get detailed status for a release via k3s-helm-helper status."""
        code, stdout, stderr = await self._run("status", name, timeout=30.0)
        if code != 0:
            logger.error("k3s-helm-helper status failed for {}: {}", name, stderr)
            raise HTTPException(
                status_code=502,
                detail={"error": "helm_status_failed", "stderr": stderr},
            )
        try:
            return ReleaseStatusDetail.from_json(stdout)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse helm status output: {}", e)
            raise HTTPException(
                status_code=502,
                detail={"error": "helm_status_parse_failed", "message": str(e)},
            ) from e
        except ValueError as e:
            logger.error("Invalid helm status format: {}", e)
            raise HTTPException(
                status_code=502,
                detail={"error": "helm_status_unexpected_format", "message": str(e)},
            ) from e

    async def _run_upgrade(self, release: str, version: Optional[str]) -> None:
        """Run helm upgrade in background. Updates _upgrade_result on completion."""
        try:
            args = [release]
            if version:
                args.append(version)
            code, stdout, stderr = await self._run(
                "upgrade", *args, timeout=self.upgrade_timeout
            )
            if code != 0:
                self._upgrade_result = (
                    UpgradeStatusEnum.FAILED,
                    stderr or stdout or f"Upgrade failed with exit code {code}",
                )
            else:
                self._upgrade_result = (UpgradeStatusEnum.COMPLETED, None)
        except asyncio.TimeoutError:
            self._upgrade_result = (
                UpgradeStatusEnum.FAILED,
                f"Upgrade timed out after {self.upgrade_timeout}s",
            )
        except Exception as e:
            logger.exception("Helm upgrade failed for {}: {}", release, e)
            self._upgrade_result = (UpgradeStatusEnum.FAILED, str(e))
        finally:
            self._upgrade_task = None

    async def start_upgrade(
        self, release: str, version: Optional[str] = None
    ) -> UpgradeStartStatus:
        """Start helm upgrade. Returns started, in_progress, or up_to_date."""
        status = UpgradeStartStatus.STARTED

        if self._upgrade_task is not None and not self._upgrade_task.done():
            status = UpgradeStartStatus.IN_PROGRESS
        else:
            if self._upgrade_result is not None:
                status_enum, _ = self._upgrade_result
                if status_enum == UpgradeStatusEnum.COMPLETED:
                    self._upgrade_result = None

            if version:
                releases = await self.list_releases()
                for r in releases:
                    if r.name == release and r.chart.endswith(f"-{version}"):
                        status = UpgradeStartStatus.UP_TO_DATE
                        break

            if status == UpgradeStartStatus.STARTED:
                self._upgrade_release = release
                self._upgrade_task = asyncio.create_task(
                    self._run_upgrade(release, version)
                )

        return status

    def get_upgrade_status(self) -> Optional[UpgradeSnapshot]:
        """Get current async upgrade status."""
        if self._upgrade_task is not None and not self._upgrade_task.done():
            return UpgradeSnapshot(
                release=self._upgrade_release or "unknown",
                status=UpgradeStatusEnum.IN_PROGRESS,
                error=None,
            )
        if self._upgrade_result is not None:
            status_enum, error = self._upgrade_result
            return UpgradeSnapshot(
                release=self._upgrade_release or "unknown",
                status=status_enum,
                error=error,
            )
        return None
