from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from sek8s.config import AttestationServiceConfig
from sek8s.models import DeviceInfo
from sek8s.providers.gpu import sanitize_gpu_id
from sek8s.services.attestation import AttestationServer


@pytest.fixture
def sample_devices():
    return [
        DeviceInfo(
            uuid="d52bd15208478ba8ca49e07ec1f002e6",
            name="NVIDIA H200",
            memory=150_754_820_096,
            major=9,
            minor=0,
            clock_rate=1_980_000.0,
            ecc=True,
            model_short_ref="h200",
        ),
        DeviceInfo(
            uuid="d1cddac2cd1195eedcfe291ce243bf32",
            name="NVIDIA H200",
            memory=150_754_820_096,
            major=9,
            minor=0,
            clock_rate=1_980_000.0,
            ecc=True,
            model_short_ref="h200",
        ),
    ]


@pytest.fixture
def attestation_client(monkeypatch, sample_devices):
    class FakeGpuDeviceProvider:
        def __init__(self, devices):
            self.devices = devices
            self.calls = []

        def get_device_info(self, gpu_ids):
            self.calls.append(gpu_ids)
            if not gpu_ids:
                return self.devices

            formatted = [sanitize_gpu_id(gpu_id) for gpu_id in gpu_ids]
            return [device for device in self.devices if device.uuid in formatted]

    provider = FakeGpuDeviceProvider(sample_devices)

    tdx_provider = MagicMock()
    tdx_provider.get_quote = AsyncMock(return_value=b"fake-quote")

    nvtrust_provider = MagicMock()
    nvtrust_provider.__enter__.return_value = nvtrust_provider
    nvtrust_provider.__exit__.return_value = False
    nvtrust_provider.get_evidence = AsyncMock(return_value='[{"evidence": "ok"}]')

    monkeypatch.setattr(
        "sek8s.services.attestation.GpuDeviceProvider",
        lambda: provider,
    )
    monkeypatch.setattr(
        "sek8s.services.attestation.TdxQuoteProvider",
        lambda: tdx_provider,
    )
    monkeypatch.setattr(
        "sek8s.services.attestation.NvEvidenceProvider",
        lambda: nvtrust_provider,
    )

    config = AttestationServiceConfig(
        hostname="test-node",
        tls_cert_path=None,
        tls_key_path=None,
        client_ca_path=None,
    )
    server = AttestationServer(config)
    client = TestClient(server.app)
    client.gpu_provider = provider
    client.tdx_provider = tdx_provider
    client.nvtrust_provider = nvtrust_provider
    return client


def test_get_devices_with_repeated_query_params(attestation_client):
    response = attestation_client.get(
        "/devices",
        params=[
            ("gpu_ids", "GPU-d52bd152-0847-8ba8-ca49-e07ec1f002e6"),
            ("gpu_ids", "GPU-d1cddac2-cd11-95ee-dcfe-291ce243bf32"),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert {device["uuid"] for device in payload} == {
        "d52bd15208478ba8ca49e07ec1f002e6",
        "d1cddac2cd1195eedcfe291ce243bf32",
    }


def test_get_devices_with_comma_separated_gpu_ids(attestation_client):
    response = attestation_client.get(
        "/devices",
        params={
            "gpu_ids": ",".join(
                [
                    "GPU-d52bd152-0847-8ba8-ca49-e07ec1f002e6",
                    "GPU-d1cddac2-cd11-95ee-dcfe-291ce243bf32",
                ]
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert {device["uuid"] for device in payload} == {
        "d52bd15208478ba8ca49e07ec1f002e6",
        "d1cddac2cd1195eedcfe291ce243bf32",
    }


def test_attest_with_comma_separated_gpu_ids(attestation_client):
    nonce = "a" * 64
    response = attestation_client.get(
        "/attest",
        params={
            "nonce": nonce,
            "gpu_ids": "GPU-1,GPU-2",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["tdx_quote"] == "ZmFrZS1xdW90ZQ=="  # base64 of fake quote
    assert (
        data["nvtrust_evidence"]
        == attestation_client.nvtrust_provider.get_evidence.return_value
    )

    attestation_client.tdx_provider.get_quote.assert_awaited_once_with(nonce)
    attestation_client.nvtrust_provider.get_evidence.assert_awaited_once_with(
        "test-node",
        nonce,
        ["GPU-1", "GPU-2"],
    )


def test_nvtrust_endpoint_with_comma_separated_gpu_ids(attestation_client):
    response = attestation_client.get(
        "/nvtrust/evidence",
        params={
            "name": "custom-node",
            "nonce": "123",
            "gpu_ids": "GPU-a,GPU-b",
        },
    )

    assert response.status_code == 200
    assert response.json() == attestation_client.nvtrust_provider.get_evidence.return_value

    attestation_client.nvtrust_provider.get_evidence.assert_awaited_once_with(
        "custom-node",
        "123",
        ["GPU-a", "GPU-b"],
    )
