"""Unit tests for attestation_proxy.signing and X-Signature header injection."""

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest
from attestation_proxy.signing import (
    HOTKEY_SIGNING_PURPOSE,
    load_miner_keypair,
    load_private_key,
    sign_response,
    sign_response_body,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPrivateKey,
    generate_private_key,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rsa_key(key_size: int = 2048) -> RSAPrivateKey:
    """Generate a fresh RSA private key for testing."""
    return generate_private_key(public_exponent=65537, key_size=key_size)


def _write_pem_key(path, key: RSAPrivateKey) -> None:
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)


def _make_httpx_response(content: bytes = b"response body", status_code: int = 200):
    response = MagicMock()
    response.content = content
    response.status_code = status_code
    response.headers = {"content-type": "application/json"}
    return response


def _make_proxy_config():
    """Return a minimal AttestationProxyConfig-like mock."""
    config = MagicMock()
    config.tls_key_path = None
    return config


def _make_server(server_class, private_key=None, miner_keypair=None):
    """Construct a proxy server instance bypassing __init__ and inject attrs."""
    shared = MagicMock()
    shared.consecutive_socket_failures = 0
    shared.http_client = AsyncMock()
    shared.unix_client = None

    server = server_class.__new__(server_class)
    server.shared = shared
    server.server_name = server_class.__name__.upper()
    server._private_key = private_key
    server._miner_keypair = miner_keypair
    server.app = MagicMock()
    return server


# 32-byte hex seed for a deterministic sr25519 keypair in tests (never a real miner seed).
_TEST_SEED = "0x" + "ab" * 32


def _make_test_keypair():
    from substrateinterface import Keypair

    return Keypair.create_from_seed(_TEST_SEED)


# ---------------------------------------------------------------------------
# signing.sign_response_body
# ---------------------------------------------------------------------------


def test_sign_response_body_round_trip():
    key = _make_rsa_key()
    body = b"hello attestation world"

    encoded = sign_response_body(key, body)

    signature = base64.b64decode(encoded)
    key.public_key().verify(signature, body, padding.PKCS1v15(), hashes.SHA256())


def test_sign_response_body_returns_ascii_string():
    key = _make_rsa_key()
    result = sign_response_body(key, b"data")
    assert isinstance(result, str)
    result.encode("ascii")  # must not raise


def test_sign_response_body_empty_body():
    key = _make_rsa_key()
    encoded = sign_response_body(key, b"")
    signature = base64.b64decode(encoded)
    key.public_key().verify(signature, b"", padding.PKCS1v15(), hashes.SHA256())


# ---------------------------------------------------------------------------
# signing.load_private_key
# ---------------------------------------------------------------------------


def test_load_private_key_success(tmp_path):
    key = _make_rsa_key(key_size=2048)
    key_file = tmp_path / "server.key"
    _write_pem_key(key_file, key)

    loaded = load_private_key(key_file)

    assert loaded is not None
    assert isinstance(loaded, RSAPrivateKey)
    assert loaded.key_size == 2048


def test_load_private_key_missing_file(tmp_path):
    result = load_private_key(tmp_path / "nonexistent.key")
    assert result is None


def test_load_private_key_invalid_content(tmp_path):
    bad_file = tmp_path / "bad.key"
    bad_file.write_bytes(b"this is not a pem key")

    result = load_private_key(bad_file)
    assert result is None


def test_load_private_key_none_path():
    result = load_private_key(None)
    assert result is None


# ---------------------------------------------------------------------------
# X-Signature header injection via proxy_request
# ---------------------------------------------------------------------------


@pytest.fixture()
def rsa_key():
    return _make_rsa_key()


@pytest.mark.asyncio
async def test_proxy_request_adds_x_signature_header(rsa_key):
    from attestation_proxy.service import ExternalProxyServer

    body = b"attestation response content"
    server = _make_server(ExternalProxyServer, private_key=rsa_key)
    server.shared.http_client.request = AsyncMock(
        return_value=_make_httpx_response(content=body)
    )

    response = await server.proxy_request(
        target_url="http://fake-upstream",
        method="GET",
        path="/attest",
        headers={},
        body=b"",
    )

    assert "x-signature" in {k.lower() for k in response.headers.keys()}
    sig_header = next(
        v for k, v in response.headers.items() if k.lower() == "x-signature"
    )
    signature = base64.b64decode(sig_header)
    rsa_key.public_key().verify(signature, body, padding.PKCS1v15(), hashes.SHA256())


def test_external_proxy_raises_on_missing_key(monkeypatch, tmp_path):
    """ExternalProxyServer must refuse to start when the TLS key cannot be loaded."""
    from attestation_proxy.service import ExternalProxyServer, SharedProxyResources

    config = _make_proxy_config()
    config.tls_key_path = tmp_path / "nonexistent.key"

    # Suppress the lifespan so __init__ doesn't try to bind ports
    monkeypatch.setattr("attestation_proxy.service.asynccontextmanager", lambda f: f)

    with pytest.raises(RuntimeError, match="TLS private key could not be loaded"):
        ExternalProxyServer(config, SharedProxyResources())


@pytest.mark.asyncio
async def test_proxy_request_propagates_signing_error(rsa_key, monkeypatch):
    """A signing failure during a request must not be silently swallowed."""
    from attestation_proxy.service import ExternalProxyServer

    server = _make_server(ExternalProxyServer, private_key=rsa_key)
    server.shared.http_client.request = AsyncMock(
        return_value=_make_httpx_response(content=b"body")
    )

    monkeypatch.setattr(
        "attestation_proxy.service.sign_response_body",
        MagicMock(side_effect=RuntimeError("crypto failure")),
    )

    with pytest.raises(RuntimeError, match="crypto failure"):
        await server.proxy_request(
            target_url="http://fake-upstream",
            method="GET",
            path="/attest",
            headers={},
            body=b"",
        )


@pytest.mark.asyncio
async def test_internal_proxy_never_signs():
    from attestation_proxy.service import InternalProxyServer

    # Internal server has _private_key = None by default (set in BaseProxyServer.__init__),
    # so even if a key existed in shared resources it would not be used.
    server = _make_server(InternalProxyServer, private_key=None)
    server.shared.http_client.request = AsyncMock(
        return_value=_make_httpx_response(content=b"body")
    )

    response = await server.proxy_request(
        target_url="http://fake-upstream",
        method="GET",
        path="/attest",
        headers={},
        body=b"",
    )

    assert "x-signature" not in {k.lower() for k in response.headers.keys()}


# ---------------------------------------------------------------------------
# Server header stripping (regression: aiohttp 3.13.4 duplicate Server header)
# ---------------------------------------------------------------------------


def _make_httpx_response_with_server_header(
    content: bytes = b"response body",
    status_code: int = 200,
    server_header: str = "uvicorn",
):
    """Upstream response that includes a Server header, as FastAPI/Uvicorn backends do."""
    response = MagicMock()
    response.content = content
    response.status_code = status_code
    response.headers = {"content-type": "application/json", "server": server_header}
    return response


@pytest.mark.asyncio
async def test_proxy_request_strips_upstream_server_header_internal():
    """Upstream Server header must not be forwarded; Uvicorn adds its own.

    Forwarding it produces a duplicate Server header in the final HTTP response,
    which aiohttp 3.13.4+ rejects with 'Duplicate Server header found'.
    """
    from attestation_proxy.service import InternalProxyServer

    server = _make_server(InternalProxyServer, private_key=None)
    server.shared.http_client.request = AsyncMock(
        return_value=_make_httpx_response_with_server_header(content=b"body")
    )

    response = await server.proxy_request(
        target_url="http://fake-upstream",
        method="GET",
        path="/server/devices",
        headers={},
        body=b"",
    )

    assert "server" not in {k.lower() for k in response.headers.keys()}


# ---------------------------------------------------------------------------
# Miner-hotkey proof-of-possession signing
# ---------------------------------------------------------------------------


def test_load_miner_keypair_none_seed():
    assert load_miner_keypair(None) is None
    assert load_miner_keypair("") is None


def test_load_miner_keypair_invalid_seed():
    assert load_miner_keypair("not-a-valid-seed") is None


def test_load_miner_keypair_from_seed():
    keypair = load_miner_keypair(_TEST_SEED)
    assert keypair is not None
    assert keypair.ss58_address == _make_test_keypair().ss58_address


def test_sign_response_round_trip():
    from substrateinterface import Keypair

    keypair = _make_test_keypair()
    ss58, nonce, signature = sign_response(keypair)

    assert ss58 == keypair.ss58_address
    assert nonce.isdigit()
    message = f"{ss58}:{nonce}:{HOTKEY_SIGNING_PURPOSE}"
    # Verify exactly as the validator's rc gate does (hex signature over the string message).
    assert Keypair(ss58_address=ss58).verify(message, bytes.fromhex(signature))


@pytest.mark.asyncio
async def test_proxy_request_adds_hotkey_headers_when_seed_present(rsa_key):
    from attestation_proxy.service import ExternalProxyServer
    from substrateinterface import Keypair

    keypair = _make_test_keypair()
    server = _make_server(
        ExternalProxyServer, private_key=rsa_key, miner_keypair=keypair
    )
    server.shared.http_client.request = AsyncMock(
        return_value=_make_httpx_response(content=b"body")
    )

    response = await server.proxy_request(
        target_url="http://fake-upstream",
        method="GET",
        path="/service/chute-service-x/verify",
        headers={},
        body=b"",
    )

    lower = {k.lower(): v for k, v in response.headers.items()}
    assert lower["x-chutes-hotkey"] == keypair.ss58_address
    assert lower["x-chutes-nonce"].isdigit()
    message = (
        f"{keypair.ss58_address}:{lower['x-chutes-nonce']}:{HOTKEY_SIGNING_PURPOSE}"
    )
    assert Keypair(ss58_address=keypair.ss58_address).verify(
        message, bytes.fromhex(lower["x-chutes-signature"])
    )


@pytest.mark.asyncio
async def test_proxy_request_no_hotkey_headers_without_seed(rsa_key):
    from attestation_proxy.service import ExternalProxyServer

    server = _make_server(ExternalProxyServer, private_key=rsa_key, miner_keypair=None)
    server.shared.http_client.request = AsyncMock(
        return_value=_make_httpx_response(content=b"body")
    )

    response = await server.proxy_request(
        target_url="http://fake-upstream",
        method="GET",
        path="/service/chute-service-x/verify",
        headers={},
        body=b"",
    )

    keys = {k.lower() for k in response.headers.keys()}
    assert "x-chutes-hotkey" not in keys
    assert "x-chutes-signature" not in keys


@pytest.mark.asyncio
async def test_proxy_request_strips_upstream_server_header_external(rsa_key):
    """Same guarantee holds for the external (signed) proxy path."""
    from attestation_proxy.service import ExternalProxyServer

    server = _make_server(ExternalProxyServer, private_key=rsa_key)
    server.shared.http_client.request = AsyncMock(
        return_value=_make_httpx_response_with_server_header(content=b"body")
    )

    response = await server.proxy_request(
        target_url="http://fake-upstream",
        method="GET",
        path="/server/devices",
        headers={},
        body=b"",
    )

    assert "server" not in {k.lower() for k in response.headers.keys()}
