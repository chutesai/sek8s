"""
Registry allowlist validator.
"""

import logging
from typing import Dict

from sek8s.validators.base import ValidatorBase, ValidationResult


logger = logging.getLogger(__name__)


class RegistryValidator(ValidatorBase):
    """Validator that checks container images against registry allowlist."""
    
    async def validate(self, admission_review: Dict) -> ValidationResult:
        """Validate that all container images are from allowed registries."""
        request = admission_review.get("request", {})
        
        # Only check pods and pod-creating resources
        kind = request.get("kind", {}).get("kind", "")
        if kind not in ["Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "ReplicaSet"]:
            return ValidationResult.allow()
        
        # Check if namespace is exempt
        namespace = request.get("namespace", "default")
        if self.config.is_namespace_exempt(namespace):
            return ValidationResult.allow(f"Namespace {namespace} is exempt")
        
        # Extract images
        obj = request.get("object", {})
        images = self.extract_images(obj)
        
        if not images:
            return ValidationResult.allow()
        
        # Check each image
        violations = []
        for image in images:
            registry = self._extract_registry(image)
            if not self._is_registry_allowed(registry):
                violations.append(f"Image {image} uses disallowed registry {registry}")
        
        if violations:
            # Check enforcement mode
            ns_policy = self.config.get_namespace_policy(namespace)
            enforcement_mode = ns_policy.get("mode", self.config.enforcement_mode)
            
            if enforcement_mode == "monitor":
                logger.info("Registry violations (monitor mode): %s", violations)
                return ValidationResult.allow(
                    warning=f"Registry violations (monitor mode): {'; '.join(violations)}"
                )
            elif enforcement_mode == "warn":
                return ValidationResult.allow(
                    warning=f"Registry violations: {'; '.join(violations)}"
                )
            else:  # enforce
                return ValidationResult.deny("; ".join(violations))
        
        return ValidationResult.allow()
    
    def _extract_registry(self, image: str) -> str:
        """Extract registry from image name."""
        # Handle different image formats
        if "/" not in image:
            # No slash means Docker Hub official image
            return "docker.io"
        
        parts = image.split("/")
        first_part = parts[0]
        
        # Check if first part is a registry (contains . or :)
        if "." in first_part or ":" in first_part or first_part == "localhost":
            return first_part
        
        # Otherwise it's Docker Hub
        return "docker.io"
    
    def _is_registry_allowed(self, registry: str) -> bool:
        """Check if registry is in allowlist."""
        # Handle wildcards
        for allowed in self.config.allowed_registries:
            if allowed.endswith("*"):
                prefix = allowed[:-1]
                if registry.startswith(prefix):
                    return True
            elif allowed == registry:
                return True
        
        return False