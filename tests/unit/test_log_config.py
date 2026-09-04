"""The guest journals are miner-readable over the status API, so loguru must
never render frame-local values into a traceback."""

import sys

import pytest
from loguru import logger
from sek8s_common.log_config import configure_logging


@pytest.fixture
def restore_logger():
    """Loguru state is global — put a default sink back for later tests."""
    yield
    logger.remove()
    logger.add(sys.__stderr__)


def test_exception_traceback_omits_local_values(capsys, restore_logger):
    configure_logging()

    def _handler():
        pod_spec = {"env": [{"name": "HF_TOKEN", "value": "s3cret-tenant-value"}]}
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.exception(
                "Unexpected error processing admission request {}", "uid-1"
            )
        return pod_spec

    _handler()
    captured = capsys.readouterr().err

    # The exception and a usable traceback still reach the journal: suppressing
    # values must not cost us the stack we need to diagnose a prod guest.
    assert "Unexpected error processing admission request uid-1" in captured
    assert "RuntimeError: boom" in captured
    assert "Traceback (most recent call last)" in captured
    assert "in _handler" in captured  # the frame...
    assert 'raise RuntimeError("boom")' in captured  # ...and its source line
    # ...but never the values of the frame's locals.
    assert "s3cret-tenant-value" not in captured
    assert "HF_TOKEN" not in captured


def test_debug_flag_only_changes_level(capsys, restore_logger):
    configure_logging(debug=True)
    logger.debug("debug line {}", "visible")
    assert "debug line visible" in capsys.readouterr().err
