"""Images submodule: ImageManager for k3s/containerd image operations."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import HTTPException
from loguru import logger

from sek8s.config import CosignVerificationConfig
from sek8s.clients.cosign import CosignClient
from sek8s.image_utils import extract_registry, normalize_registry_hostname

from .models import ImageEntry, PullSnapshot, PullStatusEnum
from .util import is_registry_allowed, parse_ctr_images_list, resolve_to_full_ref, validate_image_ref

K3S_IMAGES_HELPER = "/usr/local/bin/k3s-images-helper"


class ImageManager:
    """Manages k3s/containerd images: list, pull (with cosign), delete, prune."""

    def __init__(
        self,
        *,
        allowed_registries: List[str],
        cosign_key_path: Path,
        pull_timeout: float = 600.0,
        default_org: str = "chutes",
    ):
        self.allowed_registries = allowed_registries
        self.cosign_key_path = Path(cosign_key_path)
        self.pull_timeout = pull_timeout
        self.default_org = default_org
        self._cosign_client = CosignClient()
        self._pull_tasks: Dict[str, asyncio.Task] = {}
        self._pull_results: Dict[str, tuple[PullStatusEnum, Optional[str]]] = {}

    async def list_images(self) -> List[ImageEntry]:
        """List all images in containerd via k3s-images-helper."""
        code, stdout, stderr = await self._run("list", timeout=30.0)
        if code != 0:
            logger.error("k3s-images-helper list failed: {}", stderr)
            raise HTTPException(
                status_code=502,
                detail={"error": "ctr_list_failed", "stderr": stderr},
            )
        return parse_ctr_images_list(stdout)

    async def _run(
        self,
        subcommand: str,
        image_ref: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> tuple[int, str, str]:
        """Run k3s-images-helper (restricted wrapper). Returns (exit_code, stdout, stderr).
        Runs without sudo; system-manager must be in containerd group for socket access.
        """
        cmd = [K3S_IMAGES_HELPER, subcommand]
        if image_ref is not None:
            cmd.append(image_ref)
        to = timeout or self.pull_timeout
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=to)
        return (
            process.returncode,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )

    async def _pull_image(self, image_ref: str) -> None:
        """Run cosign verify then ctr pull."""
        try:
            # 1. Cosign verify (before pull)
            vc = CosignVerificationConfig(
                verification_method="key",
                public_key=self.cosign_key_path,
                allow_http=True,
                allow_insecure=True,
            )
            ok, _digest = await self._cosign_client.verify(image_ref, vc, timeout=60.0)
            if not ok:
                self._pull_results[image_ref] = (
                    PullStatusEnum.FAILED,
                    "Cosign verification failed: image is not signed or signature invalid",
                )
                return

            # 2. Pull (normalize registry to lowercase so ctr matches registries.yaml)
            pull_ref = normalize_registry_hostname(image_ref)
            code, stdout, stderr = await self._run(
                "pull",
                image_ref=pull_ref,
                timeout=self.pull_timeout,
            )
            if code != 0:
                self._pull_results[image_ref] = (
                    PullStatusEnum.FAILED,
                    stderr or stdout or f"Pull failed with exit code {code}",
                )
            else:
                self._pull_results[image_ref] = (PullStatusEnum.COMPLETED, None)
        except asyncio.TimeoutError:
            self._pull_results[image_ref] = (
                PullStatusEnum.FAILED,
                f"Pull timed out after {self.pull_timeout}s",
            )
        except Exception as e:
            logger.exception("Image pull failed for {}: {}", image_ref, e)
            self._pull_results[image_ref] = (PullStatusEnum.FAILED, str(e))
        finally:
            self._pull_tasks.pop(image_ref, None)

    async def start_pull(self, image: str) -> tuple[str, bool]:
        """Start image pull. Returns (status, already_present).
        Accepts short form (repo:tag, org/repo:tag) or full ref.
        """
        image_ref = resolve_to_full_ref(image, self.allowed_registries, self.default_org)
        validate_image_ref(image_ref)
        registry = extract_registry(image_ref)
        if not is_registry_allowed(registry, self.allowed_registries):
            raise HTTPException(
                status_code=403,
                detail=f"Registry {registry} is not allowed. Only validator registry images are permitted.",
            )

        # Check if already present (compare against both refs; ctr stores normalized)
        entries = await self.list_images()
        normalized_ref = normalize_registry_hostname(image_ref)
        for e in entries:
            if e.ref in (image_ref, image_ref.split("@")[0], normalized_ref, normalized_ref.split("@")[0]):
                return ("present", True)

        # Check if already in progress
        if image_ref in self._pull_tasks and not self._pull_tasks[image_ref].done():
            return ("in_progress", False)

        # Check if we have a completed/failed result
        if image_ref in self._pull_results:
            status, _ = self._pull_results[image_ref]
            if status == PullStatusEnum.COMPLETED:
                return ("present", True)
            if status == PullStatusEnum.FAILED:
                # Allow retry
                del self._pull_results[image_ref]

        # Start pull
        task = asyncio.create_task(self._pull_image(image_ref))
        self._pull_tasks[image_ref] = task
        return ("started", False)

    def get_pull_status(self, image: Optional[str] = None) -> List[PullSnapshot]:
        """Get pull status for image or all in-progress. Accepts short or full form."""
        if image:
            image_ref = resolve_to_full_ref(image, self.allowed_registries, self.default_org)
            if image_ref in self._pull_tasks:
                task = self._pull_tasks[image_ref]
                if not task.done():
                    return [PullSnapshot(image_ref=image_ref, status=PullStatusEnum.IN_PROGRESS)]
            if image_ref in self._pull_results:
                status, err = self._pull_results[image_ref]
                return [PullSnapshot(image_ref=image_ref, status=status, error=err)]
            return [PullSnapshot(image_ref=image_ref, status=PullStatusEnum.PENDING)]
        # image is None - return all

        snapshots: List[PullSnapshot] = []
        for ref, task in list(self._pull_tasks.items()):
            if not task.done():
                snapshots.append(PullSnapshot(image_ref=ref, status=PullStatusEnum.IN_PROGRESS))
        for ref, (status, err) in list(self._pull_results.items()):
            snapshots.append(PullSnapshot(image_ref=ref, status=status, error=err))
        return snapshots

    async def delete_image(self, image: str, force: bool = False) -> None:
        """Remove image by reference or ID. Accepts short or full form."""
        image_ref = resolve_to_full_ref(image, self.allowed_registries, self.default_org)
        validate_image_ref(image_ref)
        rm_ref = normalize_registry_hostname(image_ref)
        code, stdout, stderr = await self._run(
            "rm",
            image_ref=rm_ref,
            timeout=30.0,
        )
        if code != 0:
            raise HTTPException(
                status_code=502,
                detail={"error": "ctr_rm_failed", "stderr": stderr or stdout},
            )

    async def prune(self) -> tuple[int, int]:
        """Prune unused images. Returns (removed_count, freed_bytes)."""
        code, stdout, stderr = await self._run("prune", timeout=120.0)
        if code != 0:
            raise HTTPException(
                status_code=502,
                detail={"error": "ctr_prune_failed", "stderr": stderr or stdout},
            )
        # ctr doesn't easily report freed bytes; we return 0 for now
        return (0, 0)
