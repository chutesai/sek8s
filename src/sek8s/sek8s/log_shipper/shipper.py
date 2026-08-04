"""Per-pod streaming capture: tail CRI log files into a bounded in-flight window,
ship complete lines over mTLS, and checkpoint the shipped byte offset.

The log file on disk is the durable buffer; only a bounded window (``buffer_bytes``)
is ever held in RAM, so total memory is deterministic (window x pods, and there
is at most one chute pod per GPU). A slow validator naturally pauses reading
(we do not read the next window until the current one is shipped — backpressure),
and the committed offset only advances on a successful ship, so on any restart we
resume from the last durably-shipped position.

Only *complete* logical lines are ever shipped — a window boundary that cuts a
physical line, and a CRI ``P``-run whose ``F`` has not arrived, are both held back
(the offset is not advanced past them). The validator therefore never receives a
partial line and needs no partial-handling.

Validator response contract (see docs/specs/chute-log-shipper.md):
  * 204                 -> LogStreamingTerminated: validator ended streaming, stop
  * any other 2xx       -> keep sending
  * 403 / 404           -> LogStreamingRejected: shipment refused, stop
  * 413                 -> PayloadTooLarge: batch too big; split and retry the halves
  * other non-2xx / err -> transient; retry with backoff, offset unchanged
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import aiohttp
from loguru import logger

from .checkpoint import CheckpointStore
from .config import LogShipperConfig
from .exceptions import (
    LogStreamingRejected,
    LogStreamingTerminated,
    PayloadTooLarge,
    TransientShipError,
)
from .models import ChutePod, LogBatch, LogLine

_VALID_STREAMS = {"stdout", "stderr"}
_VALID_TAGS = {"F", "P"}

# HTTP 413: batch too large for the validator/proxy — split rather than retry.
_PAYLOAD_TOO_LARGE = 413
# Floor for the adaptive batch-byte ceiling so it always fits one max-size line.
_MIN_BATCH_BYTES = 32_768

# CRI log filename: "<restart>.log" (current) or "<restart>.log.<rotation-suffix>".
_LOG_NAME = re.compile(r"^(\d+)\.log(?:\.(.+))?$")


def parse_cri_line(raw: str) -> Optional[Tuple[str, str, str, str]]:
    """Parse one CRI log line into (ts, stream, tag, message).

    Format: ``<RFC3339Nano> <stdout|stderr> <F|P> <message>``. Returns None for
    blank or malformed lines.
    """
    if not raw:
        return None
    parts = raw.split(" ", 3)
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


def parse_logical_lines(data: bytes, max_line_bytes: int) -> List[Tuple[LogLine, int]]:
    """Parse complete logical (F-terminated) CRI lines from a byte window.

    Returns ``(LogLine, end_offset)`` pairs, where ``end_offset`` is the byte
    offset (relative to the start of ``data``) just past the line's terminating
    ``F`` record. Only lines through their ``F`` are returned:

      * a trailing physical line without a newline (window cut mid-line), and
      * a trailing ``P``-run whose ``F`` has not yet arrived,

    are both excluded, so the caller can advance its committed offset to the last
    returned ``end_offset`` and never ship or skip a partial. Multi-chunk logical
    lines are reassembled and truncated to ``max_line_bytes``.
    """
    results: List[Tuple[LogLine, int]] = []
    partial: Dict[str, str] = {}
    i = 0
    while True:
        j = data.find(b"\n", i)
        if j == -1:
            break  # incomplete trailing physical line — leave it for next window
        end = j + 1
        parsed = parse_cri_line(data[i:j].decode("utf-8", errors="replace"))
        if parsed is not None:
            ts, stream, tag, message = parsed
            if tag == "P":
                buf = partial.get(stream, "") + message
                partial[stream] = buf[:max_line_bytes]  # bound the accumulator
            else:
                full = partial.pop(stream, "") + message
                results.append(
                    (
                        LogLine(
                            ts=ts,
                            stream=stream,
                            log=_truncate_bytes(full, max_line_bytes),
                        ),
                        end,
                    )
                )
        i = end
    return results


def _log_sort_key(path: Path) -> Tuple[int, int, str]:
    """Order log files oldest->newest: lower restart index first; within an index,
    rotated files (by suffix) before the current file."""
    match = _LOG_NAME.match(path.name)
    if not match:
        return (0, 0, path.name)
    index = int(match.group(1))
    suffix = match.group(2)
    # rotated (has suffix) sorts before current (no suffix); rotated by suffix asc.
    return (index, 0 if suffix else 1, suffix or "")


def _log_files(pod_dir: Path, container_name: str) -> List[Path]:
    """Non-gz CRI log files for the chute container only, oldest->newest.

    Only the main `chute` container is captured — init/sidecar containers are
    skipped so the shipped stream stays single-container, hence monotonic in ts,
    which the validator's high-watermark dedupe relies on. (Supporting multiple
    containers would require the validator to store a per-line container id.)
    """
    container = pod_dir / container_name
    try:
        entries = [
            e
            for e in container.iterdir()
            if e.is_file() and ".log" in e.name and not e.name.endswith(".gz")
        ]
    except (FileNotFoundError, NotADirectoryError):
        return []
    entries.sort(key=_log_sort_key)
    return entries


@dataclass(frozen=True)
class _Entry:
    """A shippable logical line plus where it ends in its file (for the offset)."""

    line: LogLine
    inode: int
    end_offset: int


def _batch_entries(
    entries: List[_Entry], max_lines: int, max_bytes: int
) -> Iterator[List[_Entry]]:
    """Split entries into batches bounded by line count and byte size."""
    batch: List[_Entry] = []
    size = 0
    for entry in entries:
        line_bytes = len(entry.line.log.encode("utf-8", errors="replace"))
        if batch and (len(batch) >= max_lines or size + line_bytes > max_bytes):
            yield batch
            batch, size = [], 0
        batch.append(entry)
        size += line_bytes
    if batch:
        yield batch


class PodLogShipper:
    """Streams one chute pod's logs until validator termination or rejection."""

    def __init__(
        self,
        config: LogShipperConfig,
        session: aiohttp.ClientSession,
        pod: ChutePod,
        checkpoints: CheckpointStore,
    ):
        self._config = config
        self._session = session
        self._pod = pod
        self._checkpoints = checkpoints
        self._url = config.logs_url(pod.config_id)
        # committed shipped offset per inode; seeded from the persisted checkpoint.
        self._offsets: Dict[int, int] = {
            int(ino): off for ino, off in checkpoints.get(pod.config_id).items()
        }
        # Adaptive batch-byte ceiling; shrinks on a 413 so we stop re-hitting it.
        self._max_batch_bytes = config.batch_max_bytes

    async def run(self) -> None:
        """Stream loop until termination, rejection, or cancel.

        No local wall-clock backstop: termination is the validator's job (204),
        and a deleted pod is stopped by the agent cancelling this task.
        """
        logger.info(
            "Capturing logs for chute pod {} (config_id={})",
            self._pod.name,
            self._pod.config_id,
        )
        try:
            while True:
                entries, hit_budget = self._read_window()
                shipped = True
                if entries:
                    try:
                        await self._ship(entries)
                    except TransientShipError:
                        # Offsets hold at the last shipped batch; retry next cycle.
                        logger.warning(
                            "Transient ship failure for config_id={}; retrying next cycle",
                            self._pod.config_id,
                        )
                        shipped = False
                # Only skip the idle sleep while actively draining a backlog.
                if not hit_budget or not shipped:
                    await asyncio.sleep(self._config.poll_interval_seconds)
        except LogStreamingTerminated:
            logger.info(
                "Validator ended log streaming for config_id={} (activated / no longer needed)",
                self._pod.config_id,
            )
        except LogStreamingRejected as exc:
            logger.warning(
                "Log streaming rejected ({}) for config_id={}: {} — stopping capture",
                exc.status,
                self._pod.config_id,
                exc.reason,
            )
        except asyncio.CancelledError:
            logger.info("Capture cancelled for config_id={}", self._pod.config_id)
            raise

    def _read_window(self) -> Tuple[List[_Entry], bool]:
        """Read up to ``buffer_bytes`` of new data across the pod's log files.

        Reads only ``[committed_offset, EOF)`` per file (offsets keyed by inode so
        they follow rotation; reset on inode change or truncation). Committed
        offsets are NOT advanced here — only ``_ship`` advances them on success, so
        an unshipped window is simply re-read next cycle. Returns the parsed
        entries and whether the read filled the window (more may remain).
        """
        pod_dir = self._config.pod_log_root / self._pod.log_dir_name
        budget = self._config.buffer_bytes
        entries: List[_Entry] = []
        present: set[int] = set()
        hit_budget = False
        for log_file in _log_files(pod_dir, self._config.container_name):
            try:
                stat = log_file.stat()
            except OSError:  # pragma: no cover - file vanished mid-scan
                continue
            inode = stat.st_ino
            present.add(inode)
            offset = self._offsets.get(inode, 0)
            if stat.st_size < offset:  # truncated — start over
                offset = 0
            available = stat.st_size - offset
            if available <= 0:
                continue
            if budget <= 0:
                hit_budget = True
                break
            to_read = min(available, budget)
            if to_read < available:
                hit_budget = True
            try:
                with log_file.open("rb") as handle:
                    handle.seek(offset)
                    data = handle.read(to_read)
            except OSError:  # pragma: no cover - file vanished mid-read
                continue
            for line, rel_end in parse_logical_lines(data, self._config.max_line_bytes):
                entries.append(_Entry(line, inode, offset + rel_end))
            budget -= len(data)
        # Forget offsets for files that are gone (rotated out / deleted).
        for inode in [i for i in self._offsets if i not in present]:
            del self._offsets[inode]
        return entries, hit_budget

    async def _ship(self, entries: List[_Entry]) -> None:
        """Ship entries in bounded batches, committing the offset per accepted batch.

        Raises LogStreamingTerminated / LogStreamingRejected to end capture, or
        TransientShipError if a batch cannot be delivered after retries — the
        committed offsets hold at the last accepted batch so the next cycle resumes
        without gaps or dups.
        """
        for batch in _batch_entries(
            entries, self._config.batch_max_lines, self._max_batch_bytes
        ):
            await self._ship_batch(batch)

    async def _ship_batch(self, batch: List[_Entry]) -> None:
        """Ship one batch, splitting it on a 413, then commit its offsets.

        Entries in a batch are ts-ordered (single container), so each half stays
        ordered — safe for the validator's high-watermark dedupe.
        """
        try:
            await self._post_batch([entry.line for entry in batch])
        except PayloadTooLarge:
            self._max_batch_bytes = max(_MIN_BATCH_BYTES, self._max_batch_bytes // 2)
            if len(batch) > 1:
                mid = len(batch) // 2
                await self._ship_batch(batch[:mid])
                await self._ship_batch(batch[mid:])
                return
            # A single line still too large — skip it (commit past it) so we make
            # progress instead of looping; max_line_bytes already bounds content.
            logger.warning(
                "Single log line exceeds the server payload limit for config_id={}; skipping",
                self._pod.config_id,
            )
        for entry in batch:
            if entry.end_offset > self._offsets.get(entry.inode, 0):
                self._offsets[entry.inode] = entry.end_offset
        await self._checkpoints.set(self._pod.config_id, self._serialize_offsets())

    def _serialize_offsets(self) -> Dict[str, int]:
        return {str(inode): off for inode, off in self._offsets.items()}

    async def _post_batch(self, batch: List[LogLine]) -> None:
        """POST one batch with retry/backoff.

        Returns when the batch is accepted (a keep-going 2xx). Raises
        LogStreamingTerminated (204), LogStreamingRejected (403/404),
        PayloadTooLarge (413 — caller splits), or TransientShipError (exhausted).
        """
        body = LogBatch(deployment_id=self._pod.deployment_id, logs=batch).model_dump()
        timeout = aiohttp.ClientTimeout(total=self._config.request_timeout_seconds)
        for attempt in range(self._config.retry_max_attempts):
            try:
                async with self._session.post(
                    self._url, json=body, timeout=timeout
                ) as resp:
                    if resp.status == self._config.stop_status_code:
                        raise LogStreamingTerminated()
                    if resp.status in self._config.terminal_status_codes:
                        raise LogStreamingRejected(
                            resp.status, self._rejection_reason(resp.status)
                        )
                    if resp.status == _PAYLOAD_TOO_LARGE:
                        raise PayloadTooLarge()  # caller splits; don't retry as-is
                    if 200 <= resp.status < 300:
                        return
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
        raise TransientShipError()

    def _backoff(self, attempt: int) -> float:
        delay = self._config.retry_base_delay_seconds * (2**attempt)
        return min(delay, self._config.retry_max_delay_seconds)

    @staticmethod
    def _rejection_reason(status: int) -> str:
        if status == 404:
            return "unknown config_id"
        if status == 403:
            return "cert/ownership rejected"
        return "rejected"
