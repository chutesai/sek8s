"""
Unit tests for Docker Hub request tracking in admission controller.

Tests RequestTracker, DockerHubTracker, CosignClient call stats, and
the debug summary / report formatting.
"""

import pytest
from collections import defaultdict
from unittest.mock import AsyncMock, Mock, patch

from sek8s.cosign.client import CosignClient
from sek8s.services.admission_controller import AdmissionController, RequestTracker
from sek8s.validators.cosign import CosignValidator, DockerHubTracker, _ImageStats
from sek8s.validators.base import ValidationResult
from sek8s.config import AdmissionConfig, NamespacePolicy


@pytest.fixture
def tracker():
    return RequestTracker()


@pytest.fixture
def hub_tracker():
    return DockerHubTracker()


@pytest.fixture
def test_config():
    return AdmissionConfig(
        opa_url="http://localhost:8181",
        opa_timeout=5.0,
        allowed_registries=["docker.io", "gcr.io", "quay.io", "localhost:30500"],
        enforcement_mode="enforce",
    )


class TestRequestTracker:
    def test_record_allowed(self, tracker):
        tracker.record("Pod", "CREATE", "chutes", allowed=True)
        assert tracker.total_requests == 1
        assert tracker.allowed_count == 1
        assert tracker.denied_count == 0
        assert tracker.by_kind["Pod"] == 1
        assert tracker.by_operation["CREATE"] == 1
        assert tracker.by_namespace["chutes"] == 1

    def test_record_denied(self, tracker):
        tracker.record("Pod", "CREATE", "chutes", allowed=False, deny_reason="bad sig")
        assert tracker.denied_count == 1
        assert "bad sig" in tracker.denied_reasons

    def test_denied_reasons_capped(self, tracker):
        for i in range(150):
            tracker.record("Pod", "CREATE", "ns", allowed=False, deny_reason=f"reason-{i}")
        assert len(tracker.denied_reasons) == tracker._denied_reasons_cap

    def test_rotate_window(self, tracker):
        for _ in range(10):
            tracker.record("Pod", "CREATE", "default", allowed=True)
        completed = tracker.rotate_window()
        assert completed == 10
        assert tracker._prev_window_requests == 10
        assert tracker._window_requests == 0
        assert tracker._peak_window_requests == 10

    def test_rotate_window_tracks_peak(self, tracker):
        for _ in range(30):
            tracker.record("Pod", "CREATE", "default", allowed=True)
        tracker.rotate_window()
        for _ in range(5):
            tracker.record("Pod", "CREATE", "default", allowed=True)
        tracker.rotate_window()
        assert tracker._peak_window_requests == 30

    def test_get_stats(self, tracker):
        tracker.record("Pod", "CREATE", "chutes", allowed=True)
        tracker.record("Deployment", "CREATE", "monitoring", allowed=False, deny_reason="denied")
        stats = tracker.get_stats()
        assert stats["total_requests"] == 2
        assert stats["allowed"] == 1
        assert stats["denied"] == 1
        assert stats["by_kind"]["Pod"] == 1
        assert stats["by_kind"]["Deployment"] == 1
        assert "denied" in stats["recent_deny_reasons"][0]

    def test_multiple_kinds_and_namespaces(self, tracker):
        kinds = ["Pod", "ReplicaSet", "Deployment", "ConfigMap", "Secret"]
        for kind in kinds:
            for _ in range(3):
                tracker.record(kind, "CREATE", "default", allowed=True)
        assert tracker.total_requests == 15
        assert len(tracker.by_kind) == 5
        for kind in kinds:
            assert tracker.by_kind[kind] == 3


class TestDockerHubTracker:
    def test_docker_hub_attempt_cache_miss(self, hub_tracker):
        hub_tracker.record_attempt(
            "nginx:latest", "docker.io", cache_hit=False,
            kind="Pod", name="test-pod", namespace="default",
        )
        assert hub_tracker.docker_hub_verify_attempts == 1
        assert hub_tracker.docker_hub_cache_misses == 1
        assert hub_tracker.docker_hub_cache_hits == 0
        assert "nginx:latest" in hub_tracker.docker_hub_images
        stats = hub_tracker.docker_hub_images["nginx:latest"]
        assert stats.cache_misses == 1
        assert stats.is_tag_only is True

    def test_docker_hub_attempt_cache_hit(self, hub_tracker):
        hub_tracker.record_attempt(
            "nginx:latest", "docker.io", cache_hit=True,
            kind="Pod", name="test-pod", namespace="default",
        )
        assert hub_tracker.docker_hub_cache_hits == 1
        assert hub_tracker.docker_hub_cache_misses == 0

    def test_non_docker_hub_counted_separately(self, hub_tracker):
        hub_tracker.record_attempt(
            "gcr.io/distroless/base:latest", "gcr.io", cache_hit=False,
            kind="Pod", name="test-pod", namespace="default",
        )
        assert hub_tracker.docker_hub_verify_attempts == 0
        assert hub_tracker.other_registry_verify_calls == 1
        assert len(hub_tracker.docker_hub_images) == 0

    def test_digest_pinned_image_flagged(self, hub_tracker):
        hub_tracker.record_attempt(
            "nginx@sha256:abc123", "docker.io", cache_hit=False,
            kind="Pod", name="test-pod", namespace="default",
        )
        stats = hub_tracker.docker_hub_images["nginx@sha256:abc123"]
        assert stats.is_tag_only is False

    def test_triggering_resources_recorded(self, hub_tracker):
        for i in range(5):
            hub_tracker.record_attempt(
                "bitnami/kubectl:latest", "docker.io", cache_hit=False,
                kind="Pod", name=f"pod-{i}", namespace="chutes",
            )
        stats = hub_tracker.docker_hub_images["bitnami/kubectl:latest"]
        assert len(stats.recent_triggers) == 5
        assert stats.recent_triggers[0] == ("Pod", "pod-0", "chutes")

    def test_triggers_capped(self, hub_tracker):
        for i in range(25):
            hub_tracker.record_attempt(
                "nginx:latest", "docker.io", cache_hit=False,
                kind="Pod", name=f"pod-{i}", namespace="default",
            )
        stats = hub_tracker.docker_hub_images["nginx:latest"]
        assert len(stats.recent_triggers) == 20

    def test_rate_limit_recording(self, hub_tracker):
        hub_tracker.record_rate_limit()
        hub_tracker.record_rate_limit()
        assert hub_tracker.rate_limit_events == 2

    def test_get_stats_sorted_by_cache_misses(self, hub_tracker):
        for _ in range(10):
            hub_tracker.record_attempt(
                "bitnami/kubectl:latest", "docker.io", cache_hit=False,
                kind="Pod", name="p", namespace="ns",
            )
        for _ in range(3):
            hub_tracker.record_attempt(
                "nginx:latest", "docker.io", cache_hit=False,
                kind="Pod", name="p", namespace="ns",
            )
        stats = hub_tracker.get_stats()
        images = stats["docker_hub_images"]
        assert len(images) == 2
        assert images[0]["image"] == "bitnami/kubectl:latest"
        assert images[0]["cache_misses"] == 10
        assert images[1]["image"] == "nginx:latest"
        assert images[1]["cache_misses"] == 3


class TestCosignClientCallStats:
    def test_initial_stats(self):
        client = CosignClient()
        stats = client.get_call_stats()
        assert stats["total_calls"] == 0
        assert stats["by_registry"] == {}

    @pytest.mark.asyncio
    async def test_run_cosign_increments_counters(self):
        client = CosignClient()
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"", b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await client._run_cosign(
                ["cosign", "verify", "--key", "/tmp/key.pub", "nginx:latest"],
                timeout=5.0,
            )
            stats = client.get_call_stats()
            assert stats["total_calls"] == 1
            assert stats["by_registry"]["docker.io"] == 1

    @pytest.mark.asyncio
    async def test_run_cosign_tracks_different_registries(self):
        client = CosignClient()
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"", b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await client._run_cosign(
                ["cosign", "verify", "--key", "/tmp/k.pub", "nginx:latest"],
                timeout=5.0,
            )
            await client._run_cosign(
                ["cosign", "verify", "--key", "/tmp/k.pub", "gcr.io/distroless/base:latest"],
                timeout=5.0,
            )
            await client._run_cosign(
                ["cosign", "verify", "--key", "/tmp/k.pub", "parachutes/app:v1"],
                timeout=5.0,
            )
            stats = client.get_call_stats()
            assert stats["total_calls"] == 3
            assert stats["by_registry"]["docker.io"] == 2
            assert stats["by_registry"]["gcr.io"] == 1


class TestAdmissionControllerTracking:
    @pytest.mark.asyncio
    async def test_validate_admission_tracks_request(self, test_config):
        controller = AdmissionController(test_config)
        review = {
            "request": {
                "uid": "test-uid",
                "kind": {"kind": "ConfigMap"},
                "operation": "CREATE",
                "namespace": "default",
                "object": {"metadata": {"name": "test-cm"}},
            }
        }
        with patch.object(controller, "validators") as mock_validators:
            mock_v = AsyncMock()
            mock_v.validate = AsyncMock(return_value=ValidationResult.allow())
            mock_validators.__iter__ = Mock(return_value=iter([mock_v]))

            await controller.validate_admission(review)

        assert controller.tracker.total_requests == 1
        assert controller.tracker.by_kind["ConfigMap"] == 1
        assert controller.tracker.by_operation["CREATE"] == 1
        assert controller.tracker.allowed_count == 1

    @pytest.mark.asyncio
    async def test_validate_denied_tracked(self, test_config):
        controller = AdmissionController(test_config)
        review = {
            "request": {
                "uid": "test-uid",
                "kind": {"kind": "Pod"},
                "operation": "CREATE",
                "namespace": "chutes",
                "object": {
                    "metadata": {"name": "bad-pod"},
                    "spec": {"containers": [{"name": "c", "image": "evil:latest"}]},
                },
            }
        }
        with patch.object(controller, "validators") as mock_validators:
            mock_v = AsyncMock()
            mock_v.validate = AsyncMock(
                return_value=ValidationResult.deny("not allowed")
            )
            mock_validators.__iter__ = Mock(return_value=iter([mock_v]))

            await controller.validate_admission(review)

        assert controller.tracker.denied_count == 1
        assert "not allowed" in controller.tracker.denied_reasons[0]

    def test_get_debug_summary(self, test_config):
        controller = AdmissionController(test_config)
        summary = controller.get_debug_summary()
        assert "admission" in summary
        assert "cosign" in summary
        assert summary["admission"]["total_requests"] == 0
        assert summary["cosign"]["hub_tracker"]["docker_hub_cache_misses"] == 0

    def test_format_docker_hub_report(self, test_config):
        controller = AdmissionController(test_config)
        controller.tracker.record("Pod", "CREATE", "chutes", True)
        controller._cosign_validator.hub_tracker.record_attempt(
            "nginx:latest", "docker.io", cache_hit=False,
            kind="Pod", name="test-pod", namespace="chutes",
        )
        report = controller.format_docker_hub_report()
        assert "DOCKER HUB REQUEST REPORT" in report
        assert "docker.io" in report
        assert "nginx:latest" in report
        assert "TAG-ONLY" in report
        assert "Pod/test-pod (chutes)" in report
