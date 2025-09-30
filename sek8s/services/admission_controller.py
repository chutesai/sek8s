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

from aiohttp import web
from cachetools import TTLCache

from sek8s.config import AdmissionConfig
from sek8s.server import WebServer
from sek8s.validators.base import ValidatorBase
from sek8s.validators.cosign import CosignValidator
from sek8s.validators.opa import OPAValidator
from sek8s.validators.registry import RegistryValidator
from sek8s.metrics import MetricsCollector

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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
        
        # Cache for admission decisions (TTL 5 minutes)
        self.decision_cache = TTLCache(maxsize=1000, ttl=300)
        
        logger.info("Admission controller initialized with %d validators", 
                   len(self.validators))
    
    def _init_validators(self):
        """Initialize all configured validators."""
        # OPA validator (always enabled for Phase 4a)
        self.validators.append(OPAValidator(self.config))
        
        # Registry validator (lightweight, always enabled)
        self.validators.append(RegistryValidator(self.config))

        self.validators.append(CosignValidator(self.config))
        
        logger.info("Initialized validators: %s", 
                   [v.__class__.__name__ for v in self.validators])
    
    def _get_cache_key(self, admission_review: Dict) -> str:
        """Generate cache key for admission review."""
        request = admission_review.get("request", {})
        
        # Create deterministic cache key from important fields
        key_parts = [
            request.get("uid", ""),
            request.get("kind", {}).get("kind", ""),
            request.get("namespace", ""),
            request.get("name", ""),
            request.get("operation", ""),
        ]
        
        # Add resource generation for updates
        if request.get("operation") == "UPDATE":
            obj = request.get("object", {})
            key_parts.append(str(obj.get("metadata", {}).get("generation", "")))
        
        return "|".join(key_parts)
    
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
        
        logger.info("Processing admission request: uid=%s, kind=%s, operation=%s",
                   uid,
                   request.get("kind", {}).get("kind", "unknown"),
                   request.get("operation", "unknown"))
        
        try:
            # Check cache
            cache_key = self._get_cache_key(admission_review)
            if cache_key in self.decision_cache:
                logger.debug("Cache hit for request %s", uid)
                self.metrics.record_cache_hit()
                return self.decision_cache[cache_key]
            
            # Run validators in parallel
            validation_tasks = [
                validator.validate(admission_review)
                for validator in self.validators
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
                logger.debug("Validator %s: allowed=%s, messages=%d, warnings=%d",
                           validator_name, result.allowed, 
                           len(result.messages), len(result.warnings))
            
            # Build response
            response = self._build_response(
                uid=uid,
                allowed=allowed,
                messages=messages,
                warnings=warnings
            )
            
            # Cache successful validations
            if allowed and self.config.cache_enabled:
                self.decision_cache[cache_key] = response
            
            # Record metrics
            elapsed = time.time() - start_time
            self.metrics.record_admission_decision(
                allowed=allowed,
                resource_kind=request.get("kind", {}).get("kind", "unknown"),
                operation=request.get("operation", "unknown"),
                duration=elapsed
            )
            
            logger.info("Admission decision for %s: allowed=%s, duration=%.3fs",
                       uid, allowed, elapsed)
            
            return response
            
        except Exception as e:
            logger.exception("Unexpected error processing admission request %s", uid)
            
            # Fail closed on unexpected errors
            return self._build_response(
                uid=uid,
                allowed=False,
                messages=[f"Internal error: {str(e)}"],
                warnings=[]
            )
    
    def _build_response(self, uid: str, allowed: bool, 
                       messages: List[str], warnings: List[str]) -> Dict:
        """Build admission review response."""
        response = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": {
                "uid": uid,
                "allowed": allowed
            }
        }
        
        # Add status message
        if messages:
            response["response"]["status"] = {
                "message": "; ".join(messages)
            }
        
        # Add warnings (Kubernetes 1.19+)
        if warnings:
            response["response"]["warnings"] = warnings
        
        return response
    
    async def health_check(self) -> Dict:
        """Check health of all validators."""
        health_status = {
            "healthy": True,
            "validators": {}
        }
        
        for validator in self.validators:
            try:
                is_healthy = await validator.health_check()
                health_status["validators"][validator.__class__.__name__] = {
                    "healthy": is_healthy
                }
                if not is_healthy:
                    health_status["healthy"] = False
            except Exception as e:
                health_status["validators"][validator.__class__.__name__] = {
                    "healthy": False,
                    "error": str(e)
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
        self.app.router.add_post('/validate', self.handle_validate)
        self.app.router.add_post('/mutate', self.handle_mutate)  # For future use
        self.app.router.add_get('/health', self.handle_health)
        self.app.router.add_get('/ready', self.handle_ready)
        self.app.router.add_get('/metrics', self.handle_metrics)
    
    async def handle_validate(self, request: web.Request) -> web.Response:
        """Handle validation webhook requests."""
        try:
            admission_review = await request.json()
            
            # Validate request structure
            if not admission_review.get("request"):
                return web.json_response(
                    {"error": "Invalid admission review: missing request"},
                    status=400
                )
            
            # Process admission
            response = await self.controller.validate_admission(admission_review)
            
            return web.json_response(response)
            
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in request: %s", e)
            return web.json_response(
                {"error": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            logger.exception("Error handling validation request")
            
            # Return a valid admission response that denies the request
            return web.json_response({
                "apiVersion": "admission.k8s.io/v1",
                "kind": "AdmissionReview",
                "response": {
                    "uid": admission_review.get("request", {}).get("uid", "unknown"),
                    "allowed": False,
                    "status": {
                        "message": f"Internal server error: {str(e)}"
                    }
                }
            })
    
    async def handle_mutate(self, request: web.Request) -> web.Response:
        """Handle mutation webhook requests (placeholder for future)."""
        return web.json_response({
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": {
                "uid": (await request.json()).get("request", {}).get("uid", "unknown"),
                "allowed": True
            }
        })
    
    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        health_status = await self.controller.health_check()
        status_code = 200 if health_status["healthy"] else 503
        return web.json_response(health_status, status=status_code)
    
    async def handle_ready(self, request: web.Request) -> web.Response:
        """Readiness check endpoint."""
        # Simple readiness for now - could check OPA connection, etc.
        health_status = await self.controller.health_check()
        if health_status["healthy"]:
            return web.json_response({"ready": True})
        else:
            return web.json_response({"ready": False}, status=503)
    
    async def handle_metrics(self, request: web.Request) -> web.Response:
        """Prometheus metrics endpoint."""
        metrics = self.controller.metrics.export_prometheus()
        return web.Response(text=metrics, content_type='text/plain')


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