# tests/fixtures/process.py
"""
Shared test fixtures for process execution (subprocess, asyncio.create_subprocess_exec).
Mock process factory and autouse fixture to prevent real process execution in unit tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def make_mock_process(exit_code: int = 0, stdout: str = "", stderr: str = ""):
    """Create a mock process that returns given exit_code, stdout, stderr.
    Usable with asyncio.create_subprocess_exec.
    """

    class MockProcess:
        def __init__(self):
            self.returncode = exit_code

        async def communicate(self):
            return (stdout.encode(), stderr.encode())

    return MockProcess()


@pytest.fixture(autouse=True)
def mock_create_subprocess_exec():
    """Mock asyncio.create_subprocess_exec globally so unit tests never run real processes.
    Patching at the asyncio module level ensures no leaks—any code path that uses
    create_subprocess_exec hits the mock without needing per-module patch updates.
    """
    default_proc = make_mock_process(0, "", "")
    with patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_exec:
        mock_exec.return_value = default_proc
        yield mock_exec
