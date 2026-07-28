"""Persistent resume/dedupe cursor: {config_id -> last_shipped_ts}.

Timestamp-keyed (not line-index) so it survives kubelet log rotation and agent
restarts. Reconciled to the live pod set on every poll and evicted on pod
delete, so it cannot grow unbounded.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Dict, Iterable, Optional

from loguru import logger


class CursorStore:
    """Async-safe, atomically-persisted map of config_id → last shipped ts."""

    def __init__(self, path: Path):
        self._path = path
        self._data: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Load the cursor file if present; tolerate a missing/corrupt file."""
        async with self._lock:
            self._data = self._read()

    def _read(self) -> Dict[str, str]:
        try:
            raw = self._path.read_text()
        except FileNotFoundError:
            return {}
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Failed to read cursor file {}: {}", self._path, exc)
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Corrupt cursor file {}, starting empty", self._path)
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(k): str(v) for k, v in parsed.items()}

    def get(self, config_id: str) -> Optional[str]:
        return self._data.get(config_id)

    async def set(self, config_id: str, ts: str) -> None:
        """Advance the cursor for a config_id and flush to disk."""
        async with self._lock:
            current = self._data.get(config_id)
            if current is not None and ts <= current:
                return
            self._data[config_id] = ts
            self._flush()

    async def evict(self, config_id: str) -> None:
        async with self._lock:
            if self._data.pop(config_id, None) is not None:
                self._flush()

    async def reconcile(self, live_config_ids: Iterable[str]) -> int:
        """Drop cursor entries for config_ids no longer present. Returns count removed."""
        live = set(live_config_ids)
        async with self._lock:
            stale = [cid for cid in self._data if cid not in live]
            for cid in stale:
                del self._data[cid]
            if stale:
                self._flush()
        return len(stale)

    def snapshot(self) -> Dict[str, str]:
        return dict(self._data)

    def _flush(self) -> None:
        """Atomic write: tmp file + rename, so a crash never leaves a partial cursor."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, sort_keys=True))
        os.replace(tmp, self._path)
