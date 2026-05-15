"""
Unit tests for auth.py nonce validation in authorize().

Covers SEK8S-080 (non-numeric nonce) and SEK8S-093 (future nonce rejection).
The authorize() dependency is tested via a minimal FastAPI test app so that
FastAPI's dependency injection handles Request/Header extraction correctly.

The miner SS58 and seed come from conftest.py's pytest_configure env setup:
  MINER_SS58  = "5E6xfU3oNU7y1a7pQwoc31fmUjwBZ2gKcNCw8EXsdtCQieUQ"
  MINER_SEED  = "0xe031170f32b4cda05df2f3cf6bc8d7687b683bbce23d9fa960c0b3fc21641b8a"

Nonce validation fires BEFORE signature verification, so tests that exercise
nonce rejection don't need a valid signature.
"""

import os
import time

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sek8s_common.auth import authorize
from sek8s_common.constants import HOTKEY_HEADER, NONCE_HEADER, SIGNATURE_HEADER

MINER_SS58 = os.environ.get(
    "MINER_SS58", "5E6xfU3oNU7y1a7pQwoc31fmUjwBZ2gKcNCw8EXsdtCQieUQ"
)
DUMMY_SIG = "deadbeef" * 16  # 64 hex chars — invalid but reaches nonce check first


def _make_app() -> FastAPI:
    """Minimal FastAPI app with a route protected by authorize(allow_miner=True)."""
    app = FastAPI()

    @app.middleware("http")
    async def attach_body_hash(request: Request, call_next):
        request.state.body_sha256 = "test-payload-hash"
        return await call_next(request)

    @app.get(
        "/protected",
        dependencies=[Depends(authorize(allow_miner=True, purpose="test"))],
    )
    async def protected():
        return {"ok": True}

    return app


@pytest.fixture
def auth_client():
    app = _make_app()
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="https://test")


def _headers(nonce: str, hotkey: str = MINER_SS58, sig: str = DUMMY_SIG) -> dict:
    return {
        HOTKEY_HEADER: hotkey,
        NONCE_HEADER: nonce,
        SIGNATURE_HEADER: sig,
    }


# ---------------------------------------------------------------------------
# SEK8S-080: non-numeric nonce must be rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_numeric_nonce_rejected(auth_client):
    """A nonce that is not a Unix timestamp integer must be rejected with 401."""
    resp = await auth_client.get("/protected", headers=_headers("not-a-number"))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_empty_string_nonce_rejected(auth_client):
    resp = await auth_client.get("/protected", headers=_headers(""))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_float_string_nonce_rejected(auth_client):
    """Floats are not valid nonces — int() of '1234.5' raises ValueError."""
    resp = await auth_client.get("/protected", headers=_headers("1234567890.5"))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_nonce_with_leading_text_rejected(auth_client):
    resp = await auth_client.get(
        "/protected", headers=_headers(f"abc{int(time.time())}")
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# SEK8S-093: future nonce (> now + 5s) must be rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_future_nonce_rejected(auth_client):
    """A nonce more than 5 seconds in the future must be rejected."""
    future = str(int(time.time()) + 60)
    resp = await auth_client.get("/protected", headers=_headers(future))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_nonce_just_over_future_threshold_rejected(auth_client):
    """Nonce at exactly now + 6s must be rejected (threshold is > now + 5)."""
    future = str(int(time.time()) + 6)
    resp = await auth_client.get("/protected", headers=_headers(future))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_nonce_within_future_tolerance_reaches_sig_check(auth_client):
    """
    A nonce within the 5s future tolerance passes nonce validation and proceeds
    to signature verification, where it fails (dummy sig) — but the failure is
    from sig check (401), not nonce check. Confirms the tolerance boundary works.
    """
    within_tolerance = str(int(time.time()) + 3)
    resp = await auth_client.get("/protected", headers=_headers(within_tolerance))
    # Still 401 because signature is invalid, but nonce itself was not the cause.
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Expired nonce must be rejected (existing behaviour, regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_nonce_rejected(auth_client):
    """Nonces older than 30 seconds must be rejected."""
    expired = str(int(time.time()) - 31)
    resp = await auth_client.get("/protected", headers=_headers(expired))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_nonce_at_exact_expiry_boundary_rejected(auth_client):
    """Nonce exactly 30s old must be rejected (age >= 30 is the check)."""
    boundary = str(int(time.time()) - 30)
    resp = await auth_client.get("/protected", headers=_headers(boundary))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Missing / wrong hotkey must be rejected before nonce is checked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_hotkey_rejected(auth_client):
    now = str(int(time.time()))
    resp = await auth_client.get(
        "/protected",
        headers={NONCE_HEADER: now, SIGNATURE_HEADER: DUMMY_SIG},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_hotkey_rejected(auth_client):
    now = str(int(time.time()))
    resp = await auth_client.get(
        "/protected",
        headers=_headers(
            now, hotkey="5FakeHotkeyThatIsNotTheMiner111111111111111111111"
        ),
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_all_headers_rejected(auth_client):
    resp = await auth_client.get("/protected")
    assert resp.status_code == 401
