"""Orchestrator: poll CRI for chute pods, run one capture task per pod, and
reconcile the offset checkpoints to the live pod set.

The agent is fail-closed: a crictl error or a dead validator endpoint never
crashes the loop — it logs, backs off, and retries on the next poll.
"""

from __future__ import annotations

import asyncio
import ssl
from typing import Dict, List, Optional, Set, cast

import aiohttp
from loguru import logger

from .checkpoint import CheckpointStore
from .config import LogShipperConfig
from .crictl import CrictlError, list_chute_pods
from .models import ChutePod
from .shipper import PodLogShipper


def build_ssl_context(config: LogShipperConfig) -> ssl.SSLContext:
    """TLS context presenting the registry mTLS leaf as the client identity.

    Server verification stays on (default CA bundle) — the CVM proxy presents a
    publicly-trusted cert.
    """
    context = ssl.create_default_context()
    context.load_cert_chain(
        certfile=str(config.mtls_cert_path), keyfile=str(config.mtls_key_path)
    )
    return context


class LogShipperAgent:
    """Discovers chute pods and manages their per-pod capture tasks."""

    def __init__(self, config: LogShipperConfig):
        self._config = config
        self._checkpoints = CheckpointStore(config.checkpoint_path)
        # config_id -> running capture task
        self._tasks: Dict[str, asyncio.Task] = {}
        # config_id -> pod being captured (for logging)
        self._pods: Dict[str, ChutePod] = {}
        # config_ids whose capture finished (stop/backstop) — not re-spawned while live
        self._done: Set[str] = set()
        self._session: Optional[aiohttp.ClientSession] = None  # set in run()

    async def run(self) -> None:
        await self._checkpoints.load()
        context = build_ssl_context(self._config)
        connector = aiohttp.TCPConnector(ssl=context)
        async with aiohttp.ClientSession(connector=connector) as session:
            self._session = session
            try:
                while True:
                    await self._poll_once()
                    await asyncio.sleep(self._config.poll_interval_seconds)
            finally:
                await self._shutdown()

    async def _poll_once(self) -> None:
        try:
            pods = await list_chute_pods(self._config)
        except CrictlError as exc:
            logger.warning("Pod discovery failed (will retry): {}", exc)
            return

        live_ids = {pod.config_id for pod in pods}
        self._reap_finished()
        await self._drop_gone(live_ids)
        self._spawn_new(pods, live_ids)
        await self._checkpoints.reconcile(live_ids)

    def _reap_finished(self) -> None:
        """Move completed tasks out of the active set."""
        for config_id, task in list(self._tasks.items()):
            if not task.done():
                continue
            self._tasks.pop(config_id, None)
            self._pods.pop(config_id, None)
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "Capture task for config_id={} errored: {}", config_id, exc
                )
            self._done.add(config_id)

    async def _drop_gone(self, live_ids: Set[str]) -> None:
        """Cancel captures and forget state for pods that have disappeared."""
        tracked = set(self._tasks) | self._done
        for config_id in tracked - live_ids:
            task = self._tasks.pop(config_id, None)
            if task is not None and not task.done():
                task.cancel()
            self._pods.pop(config_id, None)
            self._done.discard(config_id)
            await self._checkpoints.evict(config_id)

    def _spawn_new(self, pods: List[ChutePod], live_ids: Set[str]) -> None:
        """Start capture tasks for newly-seen pods, respecting the concurrency cap."""
        candidates = [
            pod
            for pod in pods
            if pod.config_id not in self._tasks and pod.config_id not in self._done
        ]
        available = self._config.max_concurrent_pods - len(self._tasks)
        if available <= 0:
            if candidates:
                logger.warning(
                    "At capture capacity ({} pods); deferring {} pod(s) to a later poll",
                    self._config.max_concurrent_pods,
                    len(candidates),
                )
            return
        if len(candidates) > available:
            logger.warning(
                "At capture capacity ({} pods); starting {} of {} new pod(s), deferring the rest",
                self._config.max_concurrent_pods,
                available,
                len(candidates),
            )
        for pod in candidates[:available]:
            self._pods[pod.config_id] = pod
            self._tasks[pod.config_id] = asyncio.create_task(self._capture(pod))

    async def _capture(self, pod: ChutePod) -> None:
        # Captures are only spawned from _poll_once, which runs inside run() after
        # the session is created — so this is always set here.
        session = cast(aiohttp.ClientSession, self._session)
        shipper = PodLogShipper(self._config, session, pod, self._checkpoints)
        await shipper.run()

    async def _shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except (
                asyncio.CancelledError,
                Exception,
            ):  # noqa: BLE001 - best-effort drain
                pass
        self._tasks.clear()
        self._pods.clear()
