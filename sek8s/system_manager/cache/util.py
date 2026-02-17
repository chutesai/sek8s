"""Cache submodule: pure helper functions for validator API, verification, and paths."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import aiohttp
from fastapi import HTTPException
from loguru import logger

from sek8s.config import cache_config
from sek8s.services.util import sign_request

from .models import HfInfoResponse

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


async def fetch_hf_info(chute_id: str) -> HfInfoResponse:
    """GET validator /chutes/{chute_id}/hf_info and return parsed HfInfoResponse.

    Request is signed with miner credentials when MINER_SS58/MINER_SEED are set.
    """
    base = (cache_config.validator_base_url or "").strip().rstrip("/")
    if not base:
        raise HTTPException(
            status_code=503,
            detail="Validator base URL not configured (VALIDATOR_BASE_URL)",
        )
    url = f"{base}/chutes/{chute_id}/hf_info"
    headers, _ = sign_request(purpose="cache")
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


async def fetch_repo_total_size(repo_id: str, revision: str) -> int:
    """Return total byte size of repo from validator hf_repo_info; 0 on error."""
    repo_info = await fetch_repo_info(repo_id, revision)
    if not repo_info:
        return 0
    total = 0
    for item in repo_info.get("files", []):
        if item.get("path", "").startswith("_"):
            continue
        total += item.get("size") or 0
    return total


def get_symlink_hash(file_path: Path) -> Optional[str]:
    """Return the blob hash from a symlink target, or None."""
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
    """Verify cached HF model files against the validator manifest; raises on failure."""
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
        logger.warning(
            "verify_cache: snapshot dir missing — repo={}, rev={}, expected={}",
            repo_id, revision[:12], snapshot_dir,
        )
        raise ValueError(f"Cache directory not found: {snapshot_dir}")

    local_files = {}
    for path in snapshot_dir.rglob("*"):
        if path.is_file() or path.is_symlink():
            rel_path = str(path.relative_to(snapshot_dir))
            if not any(part.startswith("_") for part in Path(rel_path).parts):
                local_files[rel_path] = path

    logger.debug(
        "verify_cache: repo={}, rev={}, remote_files={}, local_files={}",
        repo_id, revision[:12], len(remote_files), len(local_files),
    )

    verified = 0
    skipped = 0
    for remote_path, (remote_hash, remote_size) in remote_files.items():
        local_path = local_files.get(remote_path)
        if not local_path or (not local_path.exists() and not local_path.is_symlink()):
            logger.info(
                "verify_cache: missing file — repo={}, rev={}, file={}",
                repo_id, revision[:12], remote_path,
            )
            raise ValueError(f"Missing file: {remote_path}")
        if remote_hash is None or len(str(remote_hash)) == 40:
            skipped += 1
            continue
        resolved = local_path.resolve()
        if remote_size is not None and resolved.stat().st_size != remote_size:
            logger.warning(
                "verify_cache: size mismatch — repo={}, rev={}, file={}, expected={}, actual={}",
                repo_id, revision[:12], remote_path, remote_size, resolved.stat().st_size,
            )
            raise ValueError(f"Size mismatch: {remote_path} (expected={remote_size}, actual={resolved.stat().st_size})")
        symlink_hash = get_symlink_hash(local_path)
        if symlink_hash and symlink_hash != remote_hash:
            logger.warning(
                "verify_cache: hash mismatch — repo={}, rev={}, file={}, expected={}, actual={}",
                repo_id, revision[:12], remote_path, remote_hash[:12], symlink_hash[:12],
            )
            raise ValueError(f"Hash mismatch: {remote_path} (expected={remote_hash[:12]}, actual={symlink_hash[:12]})")
        verified += 1

    return {"verified": verified, "skipped": skipped, "total": len(remote_files), "skipped_api_error": False}
