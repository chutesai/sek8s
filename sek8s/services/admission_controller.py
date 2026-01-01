#!/usr/bin/env python3
"""
TEE K3s Admission Controller with OPA Integration
Phase 4a - Basic Python + OPA
"""

import asyncio
import json
import logging
import time
from typing import Dict, List

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from sek8s.config import AdmissionConfig
from sek8s.server import WebServer
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


class AdmissionController:
    """Main admission controller that orchestrates validation."""

    def __init__(self, config: AdmissionConfig):
        self.config = config
        self.metrics = MetricsCollector()

        # Initialize validators
        self.validators: List[ValidatorBase] = []
        self._init_validators()

        logger.info("Admission controller initialized with %d validators", len(self.validators))

    def _init_validators(self):
        """Initialize all configured validators."""
        # OPA validator (always enabled for Phase 4a)
        self.validators.append(OPAValidator(self.config))

        # Registry validator (lightweight, always enabled)
        self.validators.append(RegistryValidator(self.config))

        self.validators.append(CosignValidator(self.config))

        logger.info("Initialized validators: %s", [v.__class__.__name__ for v in self.validators])

    async def validate_admission(self, admission_review: Dict) -> Dict:
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

        logger.debug(
            "Processing admission request: uid=%s, kind=%s, operation=%s",
            uid,
            request.get("kind", {}).get("kind", "unknown"),
            request.get("operation", "unknown"),
        )

        try:
            # Run validators in parallel
            validation_tasks = [
                validator.validate(admission_review) for validator in self.validators
            ]

            results = await asyncio.gather(*validation_tasks, return_exceptions=True)

            # Process results
            allowed = True
            messages = []
            warnings = []

            for i, result in enumerate(results):
                validator_name = self.validators[i].__class__.__name__

                if isinstance(result, Exception):
                    logger.error("Validator %s failed: %s", validator_name, result)
                    # Fail closed on validator errors
                    allowed = False
                    messages.append(f"{validator_name}: Internal error")
                    continue

                if not result.allowed:
                    allowed = False
                    messages.extend(result.messages)

                warnings.extend(result.warnings)

                # Log validator result
                logger.debug(
                    "Validator %s: allowed=%s, messages=%d, warnings=%d",
                    validator_name,
                    result.allowed,
                    len(result.messages),
                    len(result.warnings),
                )

            # Build response
            response = self._build_response(
                uid=uid, allowed=allowed, messages=messages, warnings=warnings
            )

            # Record metrics
            elapsed = time.time() - start_time
            self.metrics.record_admission_decision(
                allowed=allowed,
                resource_kind=request.get("kind", {}).get("kind", "unknown"),
                operation=request.get("operation", "unknown"),
                duration=elapsed,
            )

            logger.debug(
                "Admission decision for %s: allowed=%s, duration=%.3fs", uid, allowed, elapsed
            )

            return response

        except Exception as e:
            logger.exception("Unexpected error processing admission request %s", uid)

            # Fail closed on unexpected errors
            return self._build_response(
                uid=uid, allowed=False, messages=[f"Internal error: {str(e)}"], warnings=[]
            )

    def _build_response(
        self, uid: str, allowed: bool, messages: List[str], warnings: List[str]
    ) -> Dict:
        """Build admission review response."""
        response = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": {"uid": uid, "allowed": allowed},
        }

        # Add status message
        if messages:
            response["response"]["status"] = {"message": "; ".join(messages)}

        # Add warnings (Kubernetes 1.19+)
        if warnings:
            response["response"]["warnings"] = warnings

        return response

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

            # Validate request structure
            if not admission_review.get("request"):
                return JSONResponse(
                    content={"error": "Invalid admission review: missing request"}, 
                    status_code=400
                )

            # Process admission
            response = await self.controller.validate_admission(admission_review)

            return JSONResponse(content=response)

        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in request: %s", e)
            return JSONResponse(
                content={"error": "Invalid JSON"}, 
                status_code=400
            )
        except Exception as e:
            logger.exception("Error handling validation request")

            # Return a valid admission response that denies the request
            return JSONResponse(
                content={
                    "apiVersion": "admission.k8s.io/v1",
                    "kind": "AdmissionReview",
                    "response": {
                        "uid": admission_review.get("request", {}).get("uid", "unknown"),
                        "allowed": False,
                        "status": {"message": f"Internal server error: {str(e)}"},
                    },
                }
            )

    async def handle_mutate(self, request: Request) -> JSONResponse:
        """Handle mutation webhook requests (placeholder for future)."""
        try:
            request_data = await request.json()
            return JSONResponse(
                content={
                    "apiVersion": "admission.k8s.io/v1",
                    "kind": "AdmissionReview",
                    "response": {
                        "uid": request_data.get("request", {}).get("uid", "unknown"),
                        "allowed": True,
                    },
                }
            )
        except Exception as e:
            logger.exception("Error handling mutation request")
            return JSONResponse(
                content={"error": "Invalid request"}, 
                status_code=400
            )

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
        # Load configuration using Pydantic
        config = AdmissionConfig()

        # Setup logging level based on config
        if config.debug:
            logging.getLogger().setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled")
            logger.debug("Configuration: %s", config.export_json())

        # Validate required TLS configuration
        if not config.tls_cert_path or not config.tls_key_path:
            logger.warning("TLS certificates not configured, running in insecure mode")

        # Create and run server
        server = AdmissionWebhookServer(config)
        server.run()

    except Exception as e:
        logger.exception("Failed to start admission controller: %s", e)
        raise


if __name__ == "__main__":
    run()
