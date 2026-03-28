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

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

_ACCESS_LOG_INTERVAL_SECONDS = 60


class _BatchAccessLog(logging.Filter):
    """Suppress individual 'POST /validate 200' and 'POST /mutate 200' uvicorn
    access-log lines and instead emit a single count every 60 seconds."""

    def __init__(self) -> None:
        super().__init__()
        self._validate_ok: int = 0
        self._mutate_ok: int = 0
        self._last_flush = time.monotonic()

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        is_ok = "200" in msg
        suppress = False

        if is_ok and "/validate" in msg:
            self._validate_ok += 1
            suppress = True
        elif is_ok and "/mutate" in msg:
            self._mutate_ok += 1
            suppress = True

        self._maybe_flush()
        return not suppress

    def _maybe_flush(self) -> None:
        now = time.monotonic()
        if now - self._last_flush < _ACCESS_LOG_INTERVAL_SECONDS:
            return
        self._last_flush = now
        total = self._validate_ok + self._mutate_ok
        if total > 0:
            logger.info(
                "Admission webhooks: %d requests in last %ds (validate=%d, mutate=%d)",
                total, _ACCESS_LOG_INTERVAL_SECONDS,
                self._validate_ok, self._mutate_ok,
            )
        self._validate_ok = 0
        self._mutate_ok = 0


logging.getLogger("uvicorn.access").addFilter(_BatchAccessLog())


class AdmissionController:
    """Main admission controller that orchestrates validation."""

    def __init__(self, config: AdmissionConfig):
        self.config = config
        self.metrics = MetricsCollector()

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

            logger.debug(
                "Admission decision for %s: allowed=%s, duration=%.3fs", uid, allowed, elapsed
            )

            return response

        except Exception as e:
            logger.exception("Unexpected error processing admission request %s", uid)

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
        super().__init__(config)

    def _setup_routes(self):
        """Setup web routes."""
        self.app.add_api_route("/validate", self.handle_validate, methods=["POST"])
        self.app.add_api_route("/mutate", self.handle_mutate, methods=["POST"])
        self.app.add_api_route("/health", self.handle_health, methods=["GET"])
        self.app.add_api_route("/ready", self.handle_ready, methods=["GET"])
        self.app.add_api_route("/metrics", self.handle_metrics, methods=["GET"])

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


def run():
    """Main entry point."""
    try:
        config = AdmissionConfig()

        if config.debug:
            logging.getLogger().setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled")
            logger.debug("Configuration: %s", config.export_json())

        if not config.tls_cert_path or not config.tls_key_path:
            logger.warning("TLS certificates not configured, running in insecure mode")

        server = AdmissionWebhookServer(config)
        server.run()

    except Exception as e:
        logger.exception("Failed to start admission controller: %s", e)
        raise


if __name__ == "__main__":
    run()
