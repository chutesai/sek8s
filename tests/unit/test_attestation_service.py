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

    monkeypatch.setattr(
        "sek8s.services.attestation.GpuDeviceProvider",
        lambda: provider,
    )

    config = AttestationServiceConfig(
        hostname="test-node",
        tls_cert_path=None,
        tls_key_path=None,
        client_ca_path=None,
    )
    server = AttestationServer(config)
    return TestClient(server.app)


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
