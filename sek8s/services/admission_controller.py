#!/usr/bin/env python3
"""
TEE K3s Admission Controller with OPA Integration
Phase 4a - Basic Python + OPA
"""

import asyncio
import base64
import json
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from sek8s.config import AdmissionConfig
from sek8s.image_utils import is_digest_pinned_reference, strip_tag
from sek8s.server import WebServer
from sek8s.services.admission_models import (
    AdmissionResponseBody,
    AdmissionReviewResponse,
    AdmissionStatus,
)
from sek8s.validators.base import ValidatorBase
from sek8s.validators.cosign import CosignValidator
from sek8s.validators.opa import OPAValidator
from sek8s.validators.registry import RegistryValidator
from sek8s.metrics import MetricsCollector

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

_DOCKER_HUB_REPORT_LOG = "/var/log/admission-docker-hub-report.log"
_SUMMARY_INTERVAL_SECONDS = 60
_DOCKER_HUB_WARN_THRESHOLDS = (50, 100, 200)

hub_report_logger = logging.getLogger("docker_hub_report")


def _setup_hub_report_logger() -> None:
    """Add a file handler for Docker Hub tracking so results can be read with cat."""
    if hub_report_logger.handlers:
        return
    hub_report_logger.setLevel(logging.INFO)
    hub_report_logger.propagate = True
    try:
        fh = logging.FileHandler(_DOCKER_HUB_REPORT_LOG, mode="a")
        fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        hub_report_logger.addHandler(fh)
    except OSError:
        logger.warning("Could not open %s for Docker Hub report logging", _DOCKER_HUB_REPORT_LOG)


class _SuppressValidate200(logging.Filter):
    """Filter out noisy 'POST /validate 200' access-log lines from uvicorn."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "/validate" in msg and "200" in msg:
            return False
        return True


logging.getLogger("uvicorn.access").addFilter(_SuppressValidate200())


class RequestTracker:
    """Lightweight in-memory tracker for admission request patterns."""

    def __init__(self) -> None:
        self.boot_time = time.monotonic()
        self.total_requests: int = 0
        self.by_kind: Dict[str, int] = defaultdict(int)
        self.by_operation: Dict[str, int] = defaultdict(int)
        self.by_namespace: Dict[str, int] = defaultdict(int)
        self.allowed_count: int = 0
        self.denied_count: int = 0
        self.denied_reasons: List[str] = []
        self._denied_reasons_cap = 100

        self._window_start = time.monotonic()
        self._window_requests: int = 0
        self._prev_window_requests: int = 0

        self._warned_thresholds: set = set()
        self._startup_report_emitted = False
        self._peak_window_requests: int = 0

    def record(self, kind: str, operation: str, namespace: str,
               allowed: bool, deny_reason: str = "") -> None:
        self.total_requests += 1
        self._window_requests += 1
        self.by_kind[kind] += 1
        self.by_operation[operation] += 1
        self.by_namespace[namespace] += 1
        if allowed:
            self.allowed_count += 1
        else:
            self.denied_count += 1
            if deny_reason and len(self.denied_reasons) < self._denied_reasons_cap:
                self.denied_reasons.append(deny_reason)

    def rotate_window(self) -> int:
        """Rotate the 60s window and return requests in the completed window."""
        completed = self._window_requests
        if completed > self._peak_window_requests:
            self._peak_window_requests = completed
        self._prev_window_requests = completed
        self._window_requests = 0
        self._window_start = time.monotonic()
        return completed

    def uptime_seconds(self) -> float:
        return time.monotonic() - self.boot_time

    def get_stats(self) -> dict:
        return {
            "uptime_seconds": round(self.uptime_seconds(), 1),
            "total_requests": self.total_requests,
            "window_requests": self._window_requests,
            "peak_window_requests": self._peak_window_requests,
            "by_kind": dict(self.by_kind),
            "by_operation": dict(self.by_operation),
            "by_namespace": dict(self.by_namespace),
            "allowed": self.allowed_count,
            "denied": self.denied_count,
            "recent_deny_reasons": self.denied_reasons[-20:],
        }


class AdmissionController:
    """Main admission controller that orchestrates validation."""

    def __init__(self, config: AdmissionConfig):
        self.config = config
        self.metrics = MetricsCollector()
        self.tracker = RequestTracker()

        # Initialize validators
        self.validators: List[ValidatorBase] = []
        self._cosign_validator: Optional[CosignValidator] = None
        self._init_validators()

        logger.info("Admission controller initialized with %d validators", len(self.validators))

    def _init_validators(self):
        """Initialize all configured validators."""
        self.validators.append(OPAValidator(self.config))
        self.validators.append(RegistryValidator(self.config))

        cosign = CosignValidator(self.config)
        self.validators.append(cosign)
        self._cosign_validator = cosign

        logger.info("Initialized validators: %s", [v.__class__.__name__ for v in self.validators])

    async def validate_admission(self, admission_review: Dict) -> AdmissionReviewResponse:
        """
        Main validation entry point.

        Args:
            admission_review: Kubernetes admission review request

        Returns:
            Admission review response
        """
        start_time = time.time()
        request = admission_review.get("request", {})
        uid = request.get("uid", "unknown")
        kind = request.get("kind", {}).get("kind", "unknown")
        operation = request.get("operation", "unknown")
        namespace = request.get("namespace", "default")

        logger.debug(
            "Processing admission request: uid=%s, kind=%s, operation=%s, namespace=%s",
            uid, kind, operation, namespace,
        )

        try:
            validation_tasks = [
                validator.validate(admission_review) for validator in self.validators
            ]

            results = await asyncio.gather(*validation_tasks, return_exceptions=True)

            allowed = True
            messages = []
            warnings = []

            for i, result in enumerate(results):
                validator_name = self.validators[i].__class__.__name__

                if isinstance(result, Exception):
                    logger.error("Validator %s failed: %s", validator_name, result)
                    allowed = False
                    messages.append(f"{validator_name}: Internal error")
                    continue

                if not result.allowed:
                    allowed = False
                    messages.extend(result.messages)

                warnings.extend(result.warnings)

                logger.debug(
                    "Validator %s: allowed=%s, messages=%d, warnings=%d",
                    validator_name,
                    result.allowed,
                    len(result.messages),
                    len(result.warnings),
                )

            response = self._build_response(
                uid=uid, allowed=allowed, messages=messages, warnings=warnings
            )

            elapsed = time.time() - start_time
            self.metrics.record_admission_decision(
                allowed=allowed,
                resource_kind=kind,
                operation=operation,
                duration=elapsed,
            )

            deny_reason = "; ".join(messages) if not allowed else ""
            self.tracker.record(kind, operation, namespace, allowed, deny_reason)

            logger.debug(
                "Admission decision for %s: allowed=%s, duration=%.3fs", uid, allowed, elapsed
            )

            return response

        except Exception as e:
            logger.exception("Unexpected error processing admission request %s", uid)

            self.tracker.record(kind, operation, namespace, False, str(e))

            return self._build_response(
                uid=uid, allowed=False, messages=[f"Internal error: {str(e)}"], warnings=[]
            )

    def _build_response(
        self, uid: str, allowed: bool, messages: List[str], warnings: List[str]
    ) -> AdmissionReviewResponse:
        """Build admission review response."""
        body = AdmissionResponseBody(
            uid=uid,
            allowed=allowed,
            status=AdmissionStatus(message="; ".join(messages)) if messages else None,
            warnings=warnings or None,
        )
        return AdmissionReviewResponse(response=body)

    async def health_check(self) -> Dict:
        """Check health of all validators."""
        health_status = {"healthy": True, "validators": {}}

        for validator in self.validators:
            try:
                is_healthy = await validator.health_check()
                health_status["validators"][validator.__class__.__name__] = {"healthy": is_healthy}
                if not is_healthy:
                    health_status["healthy"] = False
            except Exception as e:
                health_status["validators"][validator.__class__.__name__] = {
                    "healthy": False,
                    "error": str(e),
                }
                health_status["healthy"] = False

        return health_status

    def get_debug_summary(self) -> dict:
        """Return full debug summary as a JSON-serializable dict."""
        summary: dict = {"admission": self.tracker.get_stats()}
        if self._cosign_validator:
            summary["cosign"] = self._cosign_validator.get_stats()
        return summary

    def format_docker_hub_report(self) -> str:
        """Format a human-readable Docker Hub impact report."""
        lines: List[str] = []
        t = self.tracker
        uptime = t.uptime_seconds()
        lines.append("")
        lines.append("=" * 64)
        lines.append("  DOCKER HUB REQUEST REPORT")
        lines.append(f"  Time since boot: {uptime:.0f}s")
        lines.append("=" * 64)
        lines.append(f"  Total admission requests: {t.total_requests}")
        lines.append(f"    Allowed: {t.allowed_count}  Denied: {t.denied_count}")

        top_kinds = sorted(t.by_kind.items(), key=lambda kv: kv[1], reverse=True)[:10]
        lines.append(f"  By kind: {', '.join(f'{k}={v}' for k, v in top_kinds)}")
        top_ns = sorted(t.by_namespace.items(), key=lambda kv: kv[1], reverse=True)[:10]
        lines.append(f"  By namespace: {', '.join(f'{k}={v}' for k, v in top_ns)}")

        if self._cosign_validator:
            ht = self._cosign_validator.hub_tracker
            lines.append("")
            lines.append("  --- Docker Hub cosign verification ---")
            lines.append(f"  Verify attempts targeting docker.io: {ht.docker_hub_verify_attempts}")
            lines.append(f"    Cache hits (no Docker Hub request): {ht.docker_hub_cache_hits}")
            lines.append(f"    Cache misses (Docker Hub API hit):  {ht.docker_hub_cache_misses}")
            lines.append(f"  Other registry verify calls: {ht.other_registry_verify_calls}")
            lines.append(f"  Rate limit events: {ht.rate_limit_events}")

            cs = self._cosign_validator._cosign_client.get_call_stats()
            dh_subprocess = cs["by_registry"].get("docker.io", 0)
            lines.append(f"  Cosign subprocess calls to docker.io: {dh_subprocess}")

            top_images = sorted(
                ht.docker_hub_images.items(),
                key=lambda kv: kv[1].cache_misses,
                reverse=True,
            )
            if top_images:
                lines.append("")
                lines.append("  Top docker.io images by Docker Hub hit count:")
                for img, s in top_images[:15]:
                    tag_label = "TAG-ONLY" if s.is_tag_only else "digest-pinned"
                    lines.append(f"    {img} ({tag_label})")
                    lines.append(f"      {s.cache_misses} Docker Hub hits / {s.attempts} attempts")
                    if s.recent_triggers:
                        trigger_counts: Dict[str, int] = defaultdict(int)
                        for k, n, ns in s.recent_triggers:
                            trigger_counts[f"{k}/{n} ({ns})"] += 1
                        top_triggers = sorted(
                            trigger_counts.items(), key=lambda kv: kv[1], reverse=True
                        )[:5]
                        triggers_str = ", ".join(f"{t} x{c}" for t, c in top_triggers)
                        lines.append(f"      triggers: {triggers_str}")

        lines.append("=" * 64)
        return "\n".join(lines)

    def build_image_pin_patches(self, request: dict) -> List[dict]:
        """Build JSON Patch operations to pin whitelisted tag-only images to their verified digest.

        Returns an empty list when there is nothing to mutate (no cosign
        validator, no whitelisted images, or no cached digest).
        """
        if not self._cosign_validator:
            return []

        obj = request.get("object", {})
        patches: List[dict] = []

        def _maybe_pin(containers: List[dict], json_prefix: str) -> None:
            for idx, container in enumerate(containers):
                image = container.get("image", "")
                if not image or is_digest_pinned_reference(image):
                    continue
                digest = self._cosign_validator.get_pinned_digest(image)
                if digest:
                    image_no_tag = strip_tag(image)
                    pinned = f"{image_no_tag}@{digest}"
                    patches.append({
                        "op": "replace",
                        "path": f"{json_prefix}/{idx}/image",
                        "value": pinned,
                    })

        spec = obj.get("spec", {})

        # Direct pod spec
        if "containers" in spec:
            _maybe_pin(spec.get("containers", []), "/spec/containers")
        if "initContainers" in spec:
            _maybe_pin(spec.get("initContainers", []), "/spec/initContainers")

        # Deployment / StatefulSet / DaemonSet / Job template
        tpl_spec = spec.get("template", {}).get("spec", {})
        if tpl_spec:
            if "containers" in tpl_spec:
                _maybe_pin(tpl_spec["containers"], "/spec/template/spec/containers")
            if "initContainers" in tpl_spec:
                _maybe_pin(tpl_spec["initContainers"], "/spec/template/spec/initContainers")

        # CronJob extra nesting
        jt_spec = spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec", {})
        if jt_spec:
            if "containers" in jt_spec:
                _maybe_pin(jt_spec["containers"], "/spec/jobTemplate/spec/template/spec/containers")
            if "initContainers" in jt_spec:
                _maybe_pin(jt_spec["initContainers"], "/spec/jobTemplate/spec/template/spec/initContainers")

        return patches


class AdmissionWebhookServer(WebServer):
    """Async web server for admission webhook."""

    def __init__(self, config: AdmissionConfig):
        self.controller = AdmissionController(config)
        self._summary_task: Optional[asyncio.Task] = None
        super().__init__(config)

    def _setup_routes(self):
        """Setup web routes."""
        self.app.add_api_route("/validate", self.handle_validate, methods=["POST"])
        self.app.add_api_route("/mutate", self.handle_mutate, methods=["POST"])
        self.app.add_api_route("/health", self.handle_health, methods=["GET"])
        self.app.add_api_route("/ready", self.handle_ready, methods=["GET"])
        self.app.add_api_route("/metrics", self.handle_metrics, methods=["GET"])
        self.app.add_api_route("/debug/summary", self.handle_debug_summary, methods=["GET"])
        self.app.add_event_handler("startup", self._start_summary_task)
        self.app.add_event_handler("shutdown", self._stop_summary_task)

    async def _start_summary_task(self) -> None:
        self._summary_task = asyncio.create_task(self._periodic_summary_loop())

    async def _stop_summary_task(self) -> None:
        if self._summary_task:
            self._summary_task.cancel()
            try:
                await self._summary_task
            except asyncio.CancelledError:
                pass

    async def _periodic_summary_loop(self) -> None:
        """Log a summary every 60s; emit threshold warnings and startup report."""
        tracker = self.controller.tracker
        cosign_v = self.controller._cosign_validator
        try:
            while True:
                await asyncio.sleep(_SUMMARY_INTERVAL_SECONDS)
                window_count = tracker.rotate_window()

                hub_misses = 0
                if cosign_v:
                    hub_misses = cosign_v.hub_tracker.docker_hub_cache_misses

                top_kinds = sorted(
                    tracker.by_kind.items(), key=lambda kv: kv[1], reverse=True
                )[:8]
                kind_str = ", ".join(f"{k}={v}" for k, v in top_kinds)

                summary_line = (
                    f"Admission summary: {tracker.total_requests} total "
                    f"(last {_SUMMARY_INTERVAL_SECONDS}s: {window_count}), "
                    f"allowed={tracker.allowed_count} denied={tracker.denied_count}, "
                    f"docker.io cosign misses={hub_misses}, "
                    f"kinds=[{kind_str}]"
                )
                logger.info(summary_line)
                hub_report_logger.info(summary_line)

                for threshold in _DOCKER_HUB_WARN_THRESHOLDS:
                    if hub_misses >= threshold and threshold not in tracker._warned_thresholds:
                        tracker._warned_thresholds.add(threshold)
                        report = self.controller.format_docker_hub_report()
                        logger.warning(
                            "Docker Hub request threshold %d exceeded (%d cache misses)%s",
                            threshold, hub_misses, report,
                        )
                        hub_report_logger.warning(
                            "Docker Hub request threshold %d exceeded (%d cache misses)%s",
                            threshold, hub_misses, report,
                        )

                if (
                    not tracker._startup_report_emitted
                    and tracker._prev_window_requests < 5
                    and tracker._peak_window_requests >= 20
                ):
                    tracker._startup_report_emitted = True
                    report = self.controller.format_docker_hub_report()
                    logger.info("Startup burst complete. %s", report)
                    hub_report_logger.info("Startup burst complete. %s", report)

        except asyncio.CancelledError:
            pass

    async def handle_validate(self, request: Request) -> JSONResponse:
        """Handle validation webhook requests."""
        try:
            admission_review = await request.json()

            if not admission_review.get("request"):
                return JSONResponse(
                    content={"error": "Invalid admission review: missing request"},
                    status_code=400,
                )

            response = await self.controller.validate_admission(admission_review)
            return JSONResponse(content=response.model_dump(exclude_none=True))

        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in request: %s", e)
            return JSONResponse(
                content={"error": "Invalid JSON"},
                status_code=400,
            )
        except Exception as e:
            logger.exception("Error handling validation request")

            uid = admission_review.get("request", {}).get("uid", "unknown")
            error_response = AdmissionReviewResponse(
                response=AdmissionResponseBody(
                    uid=uid,
                    allowed=False,
                    status=AdmissionStatus(message=f"Internal server error: {str(e)}"),
                )
            )
            return JSONResponse(content=error_response.model_dump(exclude_none=True))

    async def handle_mutate(self, request: Request) -> JSONResponse:
        """Handle mutation webhook requests.

        For whitelisted images with a cached verified digest, pin the image
        reference to that digest via JSON Patch.  All other images pass through
        unmodified.
        """
        try:
            request_data = await request.json()
            req = request_data.get("request", {})
            uid = req.get("uid", "unknown")

            patches = self.controller.build_image_pin_patches(req)

            patch_type: Optional[str] = None
            patch_data: Optional[str] = None
            if patches:
                patch_type = "JSONPatch"
                patch_data = base64.b64encode(
                    json.dumps(patches).encode()
                ).decode()
                logger.info(
                    "Mutating webhook: pinned %d image(s) for %s/%s",
                    len(patches), req.get("namespace", "?"),
                    req.get("name", req.get("object", {}).get("metadata", {}).get("generateName", "?")),
                )

            response = AdmissionReviewResponse(
                response=AdmissionResponseBody(
                    uid=uid,
                    allowed=True,
                    patchType=patch_type,
                    patch=patch_data,
                )
            )
            return JSONResponse(content=response.model_dump(exclude_none=True))
        except Exception as e:
            logger.exception("Error handling mutation request")
            error_response = AdmissionReviewResponse(
                response=AdmissionResponseBody(uid="unknown", allowed=True)
            )
            return JSONResponse(content=error_response.model_dump(exclude_none=True))

    async def handle_health(self, request: Request) -> JSONResponse:
        """Health check endpoint."""
        health_status = await self.controller.health_check()
        status_code = 200 if health_status["healthy"] else 503
        return JSONResponse(content=health_status, status_code=status_code)

    async def handle_ready(self, request: Request) -> JSONResponse:
        """Readiness check endpoint."""
        # Simple readiness for now - could check OPA connection, etc.
        health_status = await self.controller.health_check()
        if health_status["healthy"]:
            return JSONResponse(content={"ready": True})
        else:
            return JSONResponse(content={"ready": False}, status_code=503)

    async def handle_metrics(self, request: Request) -> PlainTextResponse:
        """Prometheus metrics endpoint."""
        metrics = self.controller.metrics.export_prometheus()
        return PlainTextResponse(content=metrics, media_type="text/plain")

    async def handle_debug_summary(self, request: Request) -> JSONResponse:
        """Return full Docker Hub debug summary as JSON."""
        return JSONResponse(content=self.controller.get_debug_summary())


def run():
    """Main entry point."""
    try:
        config = AdmissionConfig()

        if config.debug:
            logging.getLogger().setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled")
            logger.debug("Configuration: %s", config.export_json())

        _setup_hub_report_logger()

        logger.info(
            "Admission controller starting. Docker Hub request tracking enabled. "
            "Summary logs every %ds. GET /debug/summary for on-demand stats. "
            "Report file: %s. Set DEBUG=true for per-request verbose logging.",
            _SUMMARY_INTERVAL_SECONDS, _DOCKER_HUB_REPORT_LOG,
        )
        hub_report_logger.info("Admission controller started — Docker Hub request tracking active.")

        if not config.tls_cert_path or not config.tls_key_path:
            logger.warning("TLS certificates not configured, running in insecure mode")

        server = AdmissionWebhookServer(config)
        server.run()

    except Exception as e:
        logger.exception("Failed to start admission controller: %s", e)
        raise


if __name__ == "__main__":
    run()
