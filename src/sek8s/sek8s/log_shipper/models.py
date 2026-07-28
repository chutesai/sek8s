"""Internal data models for the chute log shipper.

The wire body is deliberately minimal — only the log lines. Everything else
(config_id, miner_hotkey, vm_name, server) is derived validator-side from the
request path + mTLS leaf + proxy, so the guest never self-asserts identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from pydantic import BaseModel


class LogLine(BaseModel):
    """One CRI log line as shipped to the validator."""

    ts: str  # RFC3339 nanosecond timestamp, e.g. 2026-07-27T00:00:00.123456789Z
    stream: str  # "stdout" | "stderr"
    log: str


class LogBatch(BaseModel):
    """Request body for POST /instances/launch_config/{config_id}/logs."""

    logs: list[LogLine]


@dataclass(frozen=True)
class ChutePod:
    """A chute pod discovered from `crictl pods -o json`.

    Only the fields the agent actually needs: config_id (→ path), the on-disk
    log-dir components (namespace/name/uid), and the sandbox state (terminal
    detection). chute-id / deployment-id labels are intentionally not carried.
    """

    config_id: str
    name: str
    uid: str
    namespace: str
    state: str = ""
    labels: Dict[str, str] = field(default_factory=dict)

    @property
    def log_dir_name(self) -> str:
        """CRI pod log directory: <namespace>_<name>_<uid>."""
        return f"{self.namespace}_{self.name}_{self.uid}"
