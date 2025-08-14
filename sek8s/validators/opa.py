"""
OPA (Open Policy Agent) validator.
"""

import aiohttp
import asyncio
import json
import logging
from typing import Dict, List, Optional

from validators.base import ValidatorBase, ValidationResult


logger = logging.getLogger(__name__)


class OPAValidator(ValidatorBase):
    """Validator that checks admission requests against OPA policies."""
    
    def __init__(self, config):
        super().__init__(config)
        self.opa_url = config.opa_url
        self.timeout = aiohttp.ClientTimeout(total=config.opa_timeout)
        self.session = None
    
    async def _ensure_session(self):
        """Ensure aiohttp session exists."""
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
    
    async def validate(self, admission_review: Dict) -> ValidationResult:
        """Validate admission request against OPA policies."""
        await self._ensure_session()
        
        request = admission_review.get("request", {})
        
        # Check if namespace is exempt
        namespace = request.get("namespace", "default")
        if self.config.is_namespace_exempt(namespace):
            logger.debug("Namespace %s is exempt from OPA validation", namespace)
            return ValidationResult.allow(f"Namespace {namespace} is exempt")
        
        # Get namespace policy
        ns_policy = self.config.get_namespace_policy(namespace)
        enforcement_mode = ns_policy.get("mode", self.config.enforcement_mode)
        
        try:
            # Prepare OPA input
            opa_input = {
                "request": request,
                "allowed_registries": self.config.allowed_registries,
                "namespace_policy": ns_policy
            }
            
            # Query OPA
            violations = await self._query_opa(opa_input)
            
            if not violations:
                return ValidationResult.allow()
            
            # Handle based on enforcement mode
            if enforcement_mode == "monitor":
                # Log but don't block
                logger.info("OPA policy violations (monitor mode): %s", violations)
                return ValidationResult.allow(
                    warning=f"Policy violations detected (monitor mode): {'; '.join(violations)}"
                )
            elif enforcement_mode == "warn":
                # Warn but allow
                return ValidationResult.allow(
                    warning=f"Policy violations detected: {'; '.join(violations)}"
                )
            else:  # enforce
                # Block the request
                return ValidationResult.deny(
                    f"Policy violations: {'; '.join(violations)}"
                )
                
        except asyncio.TimeoutError:
            logger.error("OPA request timed out")
            # Fail closed on timeout
            return ValidationResult.deny("Policy validation timeout")
        except Exception as e:
            logger.exception("Error querying OPA")
            # Fail closed on errors
            return ValidationResult.deny(f"Policy validation error: {str(e)}")
    
    async def _query_opa(self, opa_input: Dict) -> List[str]:
        """Query OPA and return list of violations."""
        violations = []
        
        # Query the main deny endpoint
        url = f"{self.opa_url}/v1/data/kubernetes/admission/deny"
        
        async with self.session.post(url, json={"input": opa_input}) as response:
            if response.status != 200:
                raise Exception(f"OPA returned status {response.status}")
            
            result = await response.json()
            
            # Extract violations from OPA response
            if result.get("result"):
                for item in result["result"]:
                    if isinstance(item, dict) and "msg" in item:
                        violations.append(item["msg"])
                    elif isinstance(item, str):
                        violations.append(item)
        
        return violations
    
    async def health_check(self) -> bool:
        """Check OPA health."""
        await self._ensure_session()
        
        try:
            url = f"{self.opa_url}/health"
            async with self.session.get(url) as response:
                return response.status == 200
        except Exception as e:
            logger.error("OPA health check failed: %s", e)
            return False
    
    def __del__(self):
        """Cleanup session on deletion."""
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())