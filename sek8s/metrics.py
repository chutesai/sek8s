"""
Metrics collection for admission controller.
"""

import time
from collections import defaultdict
from typing import Dict


class MetricsCollector:
    """Collect and export metrics for monitoring."""
    
    def __init__(self):
        # Counters
        self.admission_total = defaultdict(int)  # by allowed/denied
        self.admission_by_kind = defaultdict(int)  # by resource kind
        self.admission_by_operation = defaultdict(int)  # by operation
        self.cache_hits = 0
        self.cache_misses = 0
        
        # Histograms (simplified - just track sum and count)
        self.admission_duration_sum = 0.0
        self.admission_duration_count = 0
        
        # Errors
        self.validator_errors = defaultdict(int)  # by validator name
        
        self.start_time = time.time()
    
    def record_admission_decision(self, allowed: bool, resource_kind: str, 
                                 operation: str, duration: float):
        """Record an admission decision."""
        decision = "allowed" if allowed else "denied"
        self.admission_total[decision] += 1
        self.admission_by_kind[f"{resource_kind}_{decision}"] += 1
        self.admission_by_operation[f"{operation}_{decision}"] += 1
        
        self.admission_duration_sum += duration
        self.admission_duration_count += 1
    
    def record_cache_hit(self):
        """Record a cache hit."""
        self.cache_hits += 1
    
    def record_cache_miss(self):
        """Record a cache miss."""
        self.cache_misses += 1
    
    def record_validator_error(self, validator_name: str):
        """Record a validator error."""
        self.validator_errors[validator_name] += 1
    
    def export_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        lines = []
        
        # Info metric
        lines.append('# HELP admission_controller_info Admission controller information')
        lines.append('# TYPE admission_controller_info gauge')
        lines.append(f'admission_controller_info{{version="1.0.0"}} 1')
        
        # Uptime
        uptime = time.time() - self.start_time
        lines.append('# HELP admission_controller_uptime_seconds Uptime in seconds')
        lines.append('# TYPE admission_controller_uptime_seconds gauge')
        lines.append(f'admission_controller_uptime_seconds {uptime:.2f}')
        
        # Admission totals
        lines.append('# HELP admission_requests_total Total admission requests')
        lines.append('# TYPE admission_requests_total counter')
        for decision, count in self.admission_total.items():
            lines.append(f'admission_requests_total{{decision="{decision}"}} {count}')
        
        # By kind
        lines.append('# HELP admission_requests_by_kind_total Admission requests by kind')
        lines.append('# TYPE admission_requests_by_kind_total counter')
        for kind_decision, count in self.admission_by_kind.items():
            parts = kind_decision.rsplit("_", 1)
            if len(parts) == 2:
                kind, decision = parts
                lines.append(f'admission_requests_by_kind_total{{kind="{kind}",decision="{decision}"}} {count}')
        
        # By operation
        lines.append('# HELP admission_requests_by_operation_total Admission requests by operation')
        lines.append('# TYPE admission_requests_by_operation_total counter')
        for op_decision, count in self.admission_by_operation.items():
            parts = op_decision.rsplit("_", 1)
            if len(parts) == 2:
                operation, decision = parts
                lines.append(f'admission_requests_by_operation_total{{operation="{operation}",decision="{decision}"}} {count}')
        
        # Duration
        if self.admission_duration_count > 0:
            avg_duration = self.admission_duration_sum / self.admission_duration_count
            lines.append('# HELP admission_request_duration_seconds Request processing duration')
            lines.append('# TYPE admission_request_duration_seconds summary')
            lines.append(f'admission_request_duration_seconds_sum {self.admission_duration_sum:.4f}')
            lines.append(f'admission_request_duration_seconds_count {self.admission_duration_count}')
        
        # Cache metrics
        lines.append('# HELP admission_cache_hits_total Cache hits')
        lines.append('# TYPE admission_cache_hits_total counter')
        lines.append(f'admission_cache_hits_total {self.cache_hits}')
        
        lines.append('# HELP admission_cache_misses_total Cache misses')
        lines.append('# TYPE admission_cache_misses_total counter')
        lines.append(f'admission_cache_misses_total {self.cache_misses}')
        
        # Errors
        if self.validator_errors:
            lines.append('# HELP admission_validator_errors_total Validator errors')
            lines.append('# TYPE admission_validator_errors_total counter')
            for validator, count in self.validator_errors.items():
                lines.append(f'admission_validator_errors_total{{validator="{validator}"}} {count}')
        
        return "\n".join(lines) + "\n"
    
    def export_json(self) -> Dict:
        """Export metrics as JSON."""
        return {
            "uptime_seconds": time.time() - self.start_time,
            "admission_total": dict(self.admission_total),
            "admission_by_kind": dict(self.admission_by_kind),
            "admission_by_operation": dict(self.admission_by_operation),
            "admission_duration": {
                "sum": self.admission_duration_sum,
                "count": self.admission_duration_count,
                "average": self.admission_duration_sum / self.admission_duration_count 
                          if self.admission_duration_count > 0 else 0
            },
            "cache": {
                "hits": self.cache_hits,
                "misses": self.cache_misses
            },
            "validator_errors": dict(self.validator_errors)
        }