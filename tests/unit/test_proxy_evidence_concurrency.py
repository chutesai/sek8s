"""
Evidence requests through the proxy are concurrency-limited.

Each /attest forks a chutes-nvevidence run over every GPU, and a validator burst of one request
per GPU OOM-kills the host attestation-service. The proxy is the socket's only client, so a
shared semaphore there bounds the load from both the validator and chute pods.

Driven through real FastAPI routing: the limit depends on the evidence routes being matched
ahead of the /server/{path:path} catch-all.
"""

import asyncio
import inspect

import httpx
import pytest
from attestation_proxy.config import AttestationProxyConfig
from attestation_proxy.service import (
    BaseProxyServer,
    ExternalProxyServer,
    InternalProxyServer,
    SharedProxyResources,
)
from fastapi import FastAPI, HTTPException, Response

EVIDENCE_ROUTES = ["/server/attest", "/server/nvtrust/evidence"]


@pytest.fixture
def upstream(monkeypatch):
    """Stub the socket hop; records peak concurrency and the paths it was asked for."""
    state = {
        "in_flight": 0,
        "peak": 0,
        "paths": [],
        "expose_errors": [],
        "delay": 0.05,
        "error": None,
    }

    async def fake(self, *, path, expose_errors=False, **kwargs):
        state["expose_errors"].append(expose_errors)
        if state["error"] is not None:
            raise state["error"]
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        state["paths"].append(path)
        await asyncio.sleep(state["delay"])
        state["in_flight"] -= 1
        return Response(content=b"{}", status_code=200)

    monkeypatch.setattr(BaseProxyServer, "proxy_request", fake)
    return state


def _shared(concurrency=1, timeout=30.0):
    return SharedProxyResources(
        AttestationProxyConfig.model_construct(
            evidence_concurrency=concurrency, evidence_queue_timeout=timeout
        )
    )


def _internal(shared):
    srv = InternalProxyServer.__new__(InternalProxyServer)
    srv.shared = shared
    srv.server_name = "INTERNAL"
    srv.app = FastAPI()
    srv._setup_routes()
    return srv.app


def _external(shared):
    from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

    srv = ExternalProxyServer.__new__(ExternalProxyServer)
    srv.shared = shared
    srv.server_name = "EXTERNAL"
    srv._private_key = generate_private_key(public_exponent=65537, key_size=2048)
    srv._miner_keypair = None
    srv.app = FastAPI()
    srv._setup_routes()
    for handler in (
        srv.proxy_evidence_authenticated,
        srv.proxy_to_host_service_authenticated,
        srv.proxy_to_service_authenticated,
    ):
        dep = inspect.signature(handler).parameters["_auth"].default.dependency
        srv.app.dependency_overrides[dep] = lambda: True
    return srv.app


def _client(app, raise_app_exceptions=True):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=raise_app_exceptions
        ),
        base_url="http://proxy",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("make_app", [_internal, _external])
@pytest.mark.parametrize("route", EVIDENCE_ROUTES)
async def test_evidence_routes_are_serialized(upstream, make_app, route):
    async with _client(make_app(_shared(1))) as c:
        responses = await asyncio.gather(
            *(c.get(route, params={"nonce": "n"}) for _ in range(8))
        )

    assert [r.status_code for r in responses] == [200] * 8
    assert upstream["peak"] == 1
    assert upstream["paths"] == ["/" + route.removeprefix("/server/")] * 8


@pytest.mark.asyncio
async def test_concurrency_limit_is_configurable(upstream):
    async with _client(_internal(_shared(3))) as c:
        await asyncio.gather(*(c.get("/server/attest") for _ in range(9)))

    assert upstream["peak"] == 3


@pytest.mark.asyncio
async def test_limit_is_shared_across_ports(upstream):
    """Validator (external) and chute-pod (internal) traffic draw from one budget."""
    shared = _shared(1)
    async with _client(_external(shared)) as ext, _client(_internal(shared)) as int_:
        await asyncio.gather(
            *(c.get("/server/attest") for c in (ext, int_) * 4),
        )

    assert upstream["peak"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("make_app", [_internal, _external])
@pytest.mark.parametrize(
    "route", ["/server/health", "/server/quote", "/server/tdx/quote"]
)
async def test_cheap_routes_bypass_the_limit(upstream, make_app, route):
    """/server/health in particular must never queue behind evidence."""
    shared = _shared(1, 0.01)
    await shared.evidence_semaphore.acquire()

    async with _client(make_app(shared)) as c:
        response = await c.get(route)

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("make_app", [_internal, _external])
async def test_queue_timeout_returns_503_without_reaching_upstream(upstream, make_app):
    shared = _shared(1, 0.01)
    await shared.evidence_semaphore.acquire()

    async with _client(make_app(shared)) as c:
        response = await c.get("/server/attest")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert upstream["paths"] == []


@pytest.mark.asyncio
async def test_slot_is_released_when_upstream_fails(upstream):
    upstream["error"] = HTTPException(status_code=502, detail="x")
    shared = _shared(1, 0.01)

    async with _client(_internal(shared)) as c:
        statuses = [(await c.get("/server/attest")).status_code for _ in range(3)]

    assert statuses == [502] * 3
    assert not shared.evidence_semaphore.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route", ["/server/attest", "/server/nvtrust/evidence", "/server/quote"]
)
async def test_internal_port_ignores_expose_errors_query_param(upstream, route):
    """The flag used to be a handler kwarg, which FastAPI let any caller set."""
    async with _client(_internal(_shared())) as c:
        await c.get(route, params={"expose_errors": "true"})

    assert upstream["expose_errors"] == [False]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/server/health", "/server/devices"])
async def test_external_non_validator_routes_do_not_expose(upstream, route):
    """Unauthenticated /server/health and miner-admitting /server/devices stay closed."""
    app = _external(_shared())
    devices = next(r for r in app.routes if getattr(r, "path", "") == "/server/devices")
    dep = inspect.signature(devices.endpoint).parameters["_auth"].default.dependency
    app.dependency_overrides[dep] = lambda: True

    async with _client(app) as c:
        await c.get(route, params={"expose_errors": "true"})

    assert upstream["expose_errors"] == [False]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route",
    ["/server/attest", "/server/nvtrust/evidence", "/server/quote", "/service/x/y"],
)
async def test_external_validator_routes_expose(upstream, route):
    async with _client(_external(_shared())) as c:
        await c.get(route)

    assert upstream["expose_errors"] == [True]


@pytest.mark.asyncio
async def test_external_evidence_route_still_requires_auth(upstream):
    shared = _shared()
    app = _external(shared)
    app.dependency_overrides.clear()

    async with _client(app, raise_app_exceptions=False) as c:
        response = await c.get("/server/attest")

    assert response.status_code >= 400
    assert upstream["paths"] == []


def test_config_defaults(monkeypatch):
    monkeypatch.setenv("ALLOWED_VALIDATORS", "x")
    monkeypatch.setenv("MINER_SS58", "x")
    monkeypatch.delenv("EVIDENCE_CONCURRENCY", raising=False)
    monkeypatch.delenv("EVIDENCE_QUEUE_TIMEOUT", raising=False)

    config = AttestationProxyConfig()

    assert config.evidence_concurrency == 1
    assert config.evidence_queue_timeout == 30.0


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("ALLOWED_VALIDATORS", "x")
    monkeypatch.setenv("MINER_SS58", "x")
    monkeypatch.setenv("EVIDENCE_CONCURRENCY", "2")
    monkeypatch.setenv("EVIDENCE_QUEUE_TIMEOUT", "45")

    config = AttestationProxyConfig()

    assert config.evidence_concurrency == 2
    assert config.evidence_queue_timeout == 45.0


@pytest.mark.parametrize(
    "env", [{"EVIDENCE_CONCURRENCY": "0"}, {"EVIDENCE_QUEUE_TIMEOUT": "0"}]
)
def test_config_rejects_non_positive(monkeypatch, env):
    monkeypatch.setenv("ALLOWED_VALIDATORS", "x")
    monkeypatch.setenv("MINER_SS58", "x")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(ValueError):
        AttestationProxyConfig()
