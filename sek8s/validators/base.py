"""
Base validator interface and common functionality.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sek8s.config import AdmissionConfig


@dataclass
class ValidationResult:
    """Result of a validation check."""

    allowed: bool
    messages: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @classmethod
    def allow(cls, message: str = None, warning: str = None):
        """Create an allowed result."""
        result = cls(allowed=True)
        if message:
            result.messages.append(message)
        if warning:
            result.warnings.append(warning)
        return result

    @classmethod
    def deny(cls, message: str):
        """Create a denied result."""
        return cls(allowed=False, messages=[message])

    @classmethod
    def combine(cls, results: List["ValidationResult"]) -> "ValidationResult":
        """Combine multiple validation results."""
        combined = cls(allowed=True)

        for result in results:
            if not result.allowed:
                combined.allowed = False
            combined.messages.extend(result.messages)
            combined.warnings.extend(result.warnings)

        return combined


class ValidatorBase(ABC):
    """Base class for all validators."""

    def __init__(self, config: AdmissionConfig):
        self.config = config

    @abstractmethod
    async def validate(self, admission_review: Dict) -> ValidationResult:
        """
        Validate an admission review request.

        Args:
            admission_review: Kubernetes admission review request

        Returns:
            ValidationResult with decision and messages
        """
        pass

    async def health_check(self) -> bool:
        """
        Check if the validator is healthy.

        Returns:
            True if healthy, False otherwise
        """
        return True

    def extract_images(self, obj: Dict) -> List[str]:
        """Extract all container images from a Kubernetes object."""
        images = []

        # Handle different object types
        spec = obj.get("spec", {})

        # Direct pod spec
        if "containers" in spec:
            images.extend([c.get("image", "") for c in spec.get("containers", [])])
            images.extend([c.get("image", "") for c in spec.get("initContainers", [])])
            images.extend([c.get("image", "") for c in spec.get("ephemeralContainers", [])])

        # Deployment, StatefulSet, DaemonSet, Job, CronJob
        template = spec.get("template", {})
        if template:
            template_spec = template.get("spec", {})
            images.extend([c.get("image", "") for c in template_spec.get("containers", [])])
            images.extend([c.get("image", "") for c in template_spec.get("initContainers", [])])
            images.extend(
                [c.get("image", "") for c in template_spec.get("ephemeralContainers", [])]
            )

        # CronJob has an additional level
        job_template = spec.get("jobTemplate", {})
        if job_template:
            job_spec = job_template.get("spec", {})
            job_template_spec = job_spec.get("template", {}).get("spec", {})
            images.extend([c.get("image", "") for c in job_template_spec.get("containers", [])])
            images.extend([c.get("image", "") for c in job_template_spec.get("initContainers", [])])

        return [img for img in images if img]  # Filter out empty strings
