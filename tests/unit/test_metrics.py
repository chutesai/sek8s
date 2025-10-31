# tests/unit/test_metrics.py
"""
Unit tests for metrics collection
"""

import time
import json
import pytest
from unittest.mock import patch

from sek8s.metrics import MetricsCollector


class TestMetricsCollector:
    """Tests for MetricsCollector."""

    @pytest.fixture
    def metrics(self):
        """Create a fresh MetricsCollector instance."""
        return MetricsCollector()

    def test_record_admission_decision_allowed(self, metrics):
        """Test recording an allowed admission decision."""
        metrics.record_admission_decision(
            allowed=True, resource_kind="Pod", operation="CREATE", duration=0.5
        )

        assert metrics.admission_total["allowed"] == 1
        assert metrics.admission_by_kind["Pod_allowed"] == 1
        assert metrics.admission_by_operation["CREATE_allowed"] == 1
        assert metrics.admission_duration_sum == 0.5
        assert metrics.admission_duration_count == 1

    def test_record_admission_decision_denied(self, metrics):
        """Test recording a denied admission decision."""
        metrics.record_admission_decision(
            allowed=False, resource_kind="Deployment", operation="UPDATE", duration=0.3
        )

        assert metrics.admission_total["denied"] == 1
        assert metrics.admission_by_kind["Deployment_denied"] == 1
        assert metrics.admission_by_operation["UPDATE_denied"] == 1
        assert metrics.admission_duration_sum == 0.3
        assert metrics.admission_duration_count == 1

    def test_record_multiple_decisions(self, metrics):
        """Test recording multiple admission decisions."""
        metrics.record_admission_decision(True, "Pod", "CREATE", 0.5)
        metrics.record_admission_decision(False, "Pod", "CREATE", 0.3)
        metrics.record_admission_decision(True, "Service", "UPDATE", 0.2)

        assert metrics.admission_total["allowed"] == 2
        assert metrics.admission_total["denied"] == 1
        assert metrics.admission_by_kind["Pod_allowed"] == 1
        assert metrics.admission_by_kind["Pod_denied"] == 1
        assert metrics.admission_by_kind["Service_allowed"] == 1
        assert metrics.admission_duration_sum == 1.0
        assert metrics.admission_duration_count == 3

    def test_record_cache_hit(self, metrics):
        """Test recording cache hits."""
        metrics.record_cache_hit()
        metrics.record_cache_hit()

        assert metrics.cache_hits == 2

    def test_record_cache_miss(self, metrics):
        """Test recording cache misses."""
        metrics.record_cache_miss()
        metrics.record_cache_miss()
        metrics.record_cache_miss()

        assert metrics.cache_misses == 3

    def test_record_validator_error(self, metrics):
        """Test recording validator errors."""
        metrics.record_validator_error("OPAValidator")
        metrics.record_validator_error("RegistryValidator")
        metrics.record_validator_error("OPAValidator")

        assert metrics.validator_errors["OPAValidator"] == 2
        assert metrics.validator_errors["RegistryValidator"] == 1

    def test_export_prometheus_format(self, metrics):
        """Test exporting metrics in Prometheus format."""
        # Add some test data
        metrics.record_admission_decision(True, "Pod", "CREATE", 0.5)
        metrics.record_admission_decision(False, "Pod", "DELETE", 0.3)
        metrics.record_cache_hit()
        metrics.record_cache_miss()
        metrics.record_validator_error("OPAValidator")

        prometheus_output = metrics.export_prometheus()

        # Check for expected metric lines
        assert "admission_controller_info" in prometheus_output
        assert "admission_controller_uptime_seconds" in prometheus_output
        assert "admission_requests_total" in prometheus_output
        assert 'admission_requests_total{decision="allowed"} 1' in prometheus_output
        assert 'admission_requests_total{decision="denied"} 1' in prometheus_output
        assert "admission_requests_by_kind_total" in prometheus_output
        assert (
            'admission_requests_by_kind_total{kind="Pod",decision="allowed"} 1' in prometheus_output
        )
        assert "admission_request_duration_seconds_sum 0.8" in prometheus_output
        assert "admission_request_duration_seconds_count 2" in prometheus_output
        assert "admission_cache_hits_total 1" in prometheus_output
        assert "admission_cache_misses_total 1" in prometheus_output
        assert 'admission_validator_errors_total{validator="OPAValidator"} 1' in prometheus_output

    def test_export_json_format(self, metrics):
        """Test exporting metrics as JSON."""
        # Add some test data
        metrics.record_admission_decision(True, "Pod", "CREATE", 0.5)
        metrics.record_cache_hit()
        metrics.record_validator_error("TestValidator")

        json_output = metrics.export_json()

        assert isinstance(json_output, dict)
        assert json_output["admission_total"]["allowed"] == 1
        assert json_output["admission_by_kind"]["Pod_allowed"] == 1
        assert json_output["admission_duration"]["sum"] == 0.5
        assert json_output["admission_duration"]["count"] == 1
        assert json_output["admission_duration"]["average"] == 0.5
        assert json_output["cache"]["hits"] == 1
        assert json_output["cache"]["misses"] == 0
        assert json_output["validator_errors"]["TestValidator"] == 1

    def test_average_duration_calculation(self, metrics):
        """Test average duration calculation."""
        metrics.record_admission_decision(True, "Pod", "CREATE", 1.0)
        metrics.record_admission_decision(True, "Pod", "CREATE", 2.0)
        metrics.record_admission_decision(True, "Pod", "CREATE", 3.0)

        json_output = metrics.export_json()
        assert json_output["admission_duration"]["average"] == 2.0

    def test_average_duration_no_requests(self, metrics):
        """Test average duration when no requests recorded."""
        json_output = metrics.export_json()
        assert json_output["admission_duration"]["average"] == 0

    def test_uptime_calculation(self, metrics):
        """Test uptime calculation."""
        # Mock time to control uptime
        with patch("time.time") as mock_time:
            # Set start time
            mock_time.return_value = 1000.0
            metrics_new = MetricsCollector()

            # Set current time (5 seconds later)
            mock_time.return_value = 1005.0
            json_output = metrics_new.export_json()

            assert json_output["uptime_seconds"] == 5.0

    def test_prometheus_help_and_type_lines(self, metrics):
        """Test that Prometheus output includes HELP and TYPE lines."""
        prometheus_output = metrics.export_prometheus()
        lines = prometheus_output.split("\n")

        # Check for HELP and TYPE pairs
        help_lines = [line for line in lines if line.startswith("# HELP")]
        type_lines = [line for line in lines if line.startswith("# TYPE")]

        assert len(help_lines) > 0
        assert len(type_lines) > 0

        # Check specific metrics have both HELP and TYPE
        assert any("admission_controller_info" in line for line in help_lines)
        assert any("admission_controller_info" in line for line in type_lines)

    def test_complex_scenario(self, metrics):
        """Test a complex scenario with multiple metric types."""
        # Simulate various operations
        for i in range(10):
            metrics.record_admission_decision(
                allowed=(i % 3 != 0),  # Deny every 3rd request
                resource_kind=["Pod", "Service", "Deployment"][i % 3],
                operation=["CREATE", "UPDATE", "DELETE"][i % 3],
                duration=0.1 * (i + 1),
            )

        for i in range(5):
            metrics.record_cache_hit()

        for i in range(3):
            metrics.record_cache_miss()

        metrics.record_validator_error("OPAValidator")
        metrics.record_validator_error("OPAValidator")
        metrics.record_validator_error("RegistryValidator")

        # Verify counts
        total_allowed = sum(1 for i in range(10) if i % 3 != 0)
        total_denied = sum(1 for i in range(10) if i % 3 == 0)

        assert metrics.admission_total["allowed"] == total_allowed
        assert metrics.admission_total["denied"] == total_denied
        assert metrics.cache_hits == 5
        assert metrics.cache_misses == 3
        assert metrics.validator_errors["OPAValidator"] == 2
        assert metrics.validator_errors["RegistryValidator"] == 1

        # Check that export functions work without errors
        prometheus_output = metrics.export_prometheus()
        assert len(prometheus_output) > 0

        json_output = metrics.export_json()
        assert isinstance(json_output, dict)
