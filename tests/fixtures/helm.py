# tests/fixtures/helm.py
"""
Shared test fixtures for HelmManager and helm-related tests.
"""

from __future__ import annotations

import pytest

from sek8s.system_manager.helm.manager import HelmManager

# Sample JSON outputs from k3s-helm-helper for default fixture behavior
HELM_LIST_EMPTY = "[]"
HELM_LIST_ONE_RELEASE = (
    '[{"name":"chutes","namespace":"chutes","chart":"chutes-miner-gpu-0.2.1",'
    '"status":"deployed","revision":3,"updated":"2026-03-20T12:00:00Z","app_version":"1.0.0"}]'
)
HELM_STATUS_DEPLOYED = (
    '{"info":{"status":"deployed","revision":3},'
    '"status":"deployed","chart":"chutes-miner-gpu-0.2.1","app_version":"1.0.0"}'
)


@pytest.fixture
def helm_manager():
    """HelmManager instance with short upgrade timeout for tests."""
    return HelmManager(upgrade_timeout=1.0)
