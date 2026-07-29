"""Per-pod capture: tail CRI log files, batch, ship over mTLS, honor the cutoff.

Cutoff contract (see docs/specs/chute-log-shipper.md):
  * validator returns 204  -> stop capturing this pod
  * any other 2xx          -> keep sending
  * non-2xx / conn error   -> transient; retry with backoff, cursor unchanged
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Tuple

import aiohttp
from loguru import logger

from .config import LogShipperConfig
from .cursor import CursorStore
from .models import ChutePod, LogBatch, LogLine

_VALID_STREAMS = {"stdout", "stderr"}
_VALID_TAGS = {"F", "P"}


def parse_cri_line(raw: str) -> Optional[Tuple[str, str, str, str]]:
    """Parse one CRI log line into (ts, stream, tag, message).

    Format: ``<RFC3339Nano> <stdout|stderr> <F|P> <message>``. Returns None for
    blank or malformed lines.
    """
    line = raw.rstrip("\n")
    if not line:
        return None
    parts = line.split(" ", 3)
    if len(parts) < 3:
        return None
    ts, stream, tag = parts[0], parts[1], parts[2]
    message = parts[3] if len(parts) == 4 else ""
    if stream not in _VALID_STREAMS or tag not in _VALID_TAGS:
        return None
    return ts, stream, tag, message


def _truncate_bytes(message: str, max_bytes: int) -> str:
    encoded = message.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return message
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _container_log_files(pod_dir: Path) -> List[Path]:
    """All plain (non-gz) CRI log files under a pod's container subdirectories."""
    files: List[Path] = []
    try:
        containers = sorted(p for p in pod_dir.iterdir() if p.is_dir())
    except (FileNotFoundError, NotADirectoryError):
        return files
    for container in containers:
        try:
            entries = sorted(container.iterdir())
        except OSError:  # pragma: no cover - defensive
            continue
        for entry in entries:
            if (
                entry.is_file()
                and ".log" in entry.name
                and not entry.name.endswith(".gz")
            ):
                files.append(entry)
    return files


def read_new_lines(
    pod: ChutePod, config: LogShipperConfig, since_ts: str
) -> List[LogLine]:
    """Read log lines with ts > since_ts, reassembling partial (P) chunks.

    Bounded by ``max_lines_per_poll``; the remainder is picked up next poll.
    Returned lines are globally sorted by timestamp.
    """
    pod_dir = config.pod_log_root / pod.log_dir_name
    collected: List[LogLine] = []
    for log_file in _container_log_files(pod_dir):
        try:
            content = log_file.read_text(errors="replace")
        except OSError:  # pragma: no cover - file vanished mid-read
            continue
        partial: dict[str, str] = {}
        for raw in content.splitlines():
            parsed = parse_cri_line(raw)
            if parsed is None:
                continue
            ts, stream, tag, message = parsed
            if tag == "P":
                partial[stream] = partial.get(stream, "") + message
                continue
            full = partial.pop(stream, "") + message
            if ts <= since_ts:
                continue
            collected.append(
                LogLine(
                    ts=ts,
                    stream=stream,
                    log=_truncate_bytes(full, config.max_line_bytes),
                )
            )
    collected.sort(key=lambda line: line.ts)
    if len(collected) > config.max_lines_per_poll:
        collected = collected[: config.max_lines_per_poll]
    return collected


def _chunk(
    lines: List[LogLine], max_lines: int, max_bytes: int
) -> Iterator[List[LogLine]]:
    """Split ts-ordered lines into batches bounded by line count and byte size."""
    batch: List[LogLine] = []
    size = 0
    for line in lines:
        line_bytes = len(line.log.encode("utf-8", errors="replace"))
        if batch and (len(batch) >= max_lines or size + line_bytes > max_bytes):
            yield batch
            batch, size = [], 0
        batch.append(line)
        size += line_bytes
    if batch:
        yield batch


class PodLogShipper:
    """Captures and ships one chute pod's logs until cutoff / terminal / backstop."""

    def __init__(
        self,
        config: LogShipperConfig,
        session: aiohttp.ClientSession,
        pod: ChutePod,
        cursor: CursorStore,
        *,
        now_fn: Callable[[], float] = time.monotonic,
    ):
        self._config = config
        self._session = session
        self._pod = pod
        self._cursor = cursor
        self._now = now_fn
        self._url = config.logs_url(pod.config_id)

    async def run(self) -> str:
        """Capture loop. Returns the reason it stopped (for logging/tests)."""
        started = self._now()
        logger.info(
            "Capturing logs for chute pod {} (config_id={})",
            self._pod.name,
            self._pod.config_id,
        )
        try:
            while True:
                since = self._cursor.get(self._pod.config_id) or ""
                lines = read_new_lines(self._pod, self._config, since)
                if lines:
                    action = await self._ship(lines)
                    if action == "stop":
                        logger.info(
                            "Validator signalled cutoff for config_id={}",
                            self._pod.config_id,
                        )
                        return "stop"
                    if action == "terminal":
                        # Reason already logged in _post_batch; stop retrying.
                        return "terminal"
                if self._now() - started >= self._config.max_capture_seconds:
                    logger.info(
                        "Max capture duration reached for config_id={}",
                        self._pod.config_id,
                    )
                    return "max_duration"
                await asyncio.sleep(self._config.poll_interval_seconds)
        except asyncio.CancelledError:
            logger.info("Capture cancelled for config_id={}", self._pod.config_id)
            raise

    async def _ship(self, lines: List[LogLine]) -> str:
        """Ship all new lines in bounded batches.

        Returns 'stop' (cutoff), 'terminal' (403/404 reject), 'ok', or 'retry'.
        """
        for batch in _chunk(
            lines, self._config.batch_max_lines, self._config.batch_max_bytes
        ):
            result = await self._post_batch(batch)
            if result == "fail":
                # Transient: leave the cursor so the same lines are re-read next poll.
                return "retry"
            if result == "terminal":
                # Reject that can never succeed — do not advance the cursor.
                return "terminal"
            # Both 'stop' (204) and 'ok' (other 2xx) mean the batch was accepted.
            await self._cursor.set(self._pod.config_id, batch[-1].ts)
            if result == "stop":
                return "stop"
        return "ok"

    async def _post_batch(self, batch: List[LogLine]) -> str:
        """POST one batch with retry/backoff.

        Returns 'stop' (204), 'terminal' (403/404), 'ok' (other 2xx), or 'fail'
        (transient error exhausted).
        """
        body = LogBatch(deployment_id=self._pod.deployment_id, logs=batch).model_dump()
        timeout = aiohttp.ClientTimeout(total=self._config.request_timeout_seconds)
        for attempt in range(self._config.retry_max_attempts):
            try:
                async with self._session.post(
                    self._url, json=body, timeout=timeout
                ) as resp:
                    if resp.status == self._config.stop_status_code:
                        return "stop"
                    if resp.status in self._config.terminal_status_codes:
                        logger.warning(
                            "Terminal reject ({}) for config_id={}: {} — stopping capture",
                            resp.status,
                            self._pod.config_id,
                            self._terminal_reason(resp.status),
                        )
                        return "terminal"
                    if 200 <= resp.status < 300:
                        return "ok"
                    logger.warning(
                        "Log ship for config_id={} got status {}",
                        self._pod.config_id,
                        resp.status,
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning(
                    "Log ship for config_id={} failed: {}", self._pod.config_id, exc
                )
            if attempt + 1 < self._config.retry_max_attempts:
                await asyncio.sleep(self._backoff(attempt))
        return "fail"

    def _backoff(self, attempt: int) -> float:
        delay = self._config.retry_base_delay_seconds * (2**attempt)
        return min(delay, self._config.retry_max_delay_seconds)

    @staticmethod
    def _terminal_reason(status: int) -> str:
        if status == 404:
            return "unknown config_id"
        if status == 403:
            return "cert/ownership rejected"
        return "terminal reject"
