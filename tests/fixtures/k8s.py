# conftest.py
"""
Shared test fixtures for TEE Admission Controller tests
"""

import os
import pytest
from unittest.mock import Mock
from sek8s.admission.admission_controller import AdmissionController


@pytest.fixture(autouse=True)
def tee_settings():
    """Create test settings."""
    os.environ.update({
        "TLS_CERT_FILE": "/tmp/test.crt",
        "TLS_PRIVATE_KEY_FILE": "/tmp/test.key",
        "ALLOWED_REGISTRIES": "docker.io,validator.registry.local",
        "VERIFY_BINARY_HASH": "False"
    })

@pytest.fixture(autouse=True)
def opa_settings():
    """Create test settings."""
    
    os.environ.update({
        "POLICY_DIR": "./opa/policies"
    })


@pytest.fixture
def admission_controller() -> AdmissionController:
    """Create admission controller instance for testing."""
    
    controller = AdmissionController()

    return controller


@pytest.fixture
def valid_pod():
    """Create a valid pod for testing."""
    return {
        "kind": "Pod",
        "metadata": {
            "name": "test-pod",
            "labels": {
                "tee.verified": "true"
            }
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "validator.registry.local/app:latest",
                    "securityContext": {
                        "privileged": False
                    }
                }
            ]
        }
    }


@pytest.fixture
def pod_missing_tee_label():
    """Create a pod without TEE verification label."""
    return {
        "kind": "Pod", 
        "metadata": {
            "name": "test-pod",
            "labels": {}  # Missing tee.verified label
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "validator.registry.local/app:latest"
                }
            ]
        }
    }


@pytest.fixture
def pod_untrusted_registry():
    """Create a pod from untrusted registry."""
    return {
        "kind": "Pod",
        "metadata": {
            "name": "test-pod", 
            "labels": {
                "tee.verified": "true"
            }
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "unvalidator.registry.local/malicious:latest"  # Untrusted registry
                }
            ]
        }
    }


@pytest.fixture
def privileged_pod():
    """Create a privileged pod."""
    return {
        "kind": "Pod",
        "metadata": {
            "name": "test-pod",
            "labels": {
                "tee.verified": "true"
            }
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "validator.registry.local/app:latest",
                    "securityContext": {
                        "privileged": True  # Privileged container
                    }
                }
            ]
        }
    }


@pytest.fixture
def host_network_pod():
    """Create a pod with host network."""
    return {
        "kind": "Pod",
        "metadata": {
            "name": "test-pod",
            "labels": {
                "tee.verified": "true"
            }
        },
        "spec": {
            "hostNetwork": True,  # Host network access
            "containers": [
                {
                    "name": "app",
                    "image": "validator.registry.local/app:latest"
                }
            ]
        }
    }


@pytest.fixture
def valid_deployment():
    """Create a valid deployment for testing."""
    return {
        "kind": "Deployment",
        "metadata": {
            "name": "test-deployment"
        },
        "spec": {
            "template": {
                "metadata": {
                    "labels": {
                        "tee.verified": "true"
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "validator.registry.local/app:latest"
                        }
                    ]
                }
            }
        }
    }


@pytest.fixture
def service_resource():
    """Create a service resource for testing."""
    return {
        "kind": "Service",
        "metadata": {
            "name": "test-service"
        },
        "spec": {
            "selector": {
                "app": "test"
            }
        }
    }


@pytest.fixture
def admission_review_template():
    """Create an admission review template."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-123",
            "operation": "CREATE",
            "object": None  # To be filled by specific tests
        }
    }


def create_request(resource_object, uid="test-uid-123", operation="CREATE"):
    """Helper function to create admission request."""
    return {
        "request": {
            "uid": uid,
            "object": resource_object,
            "operation": operation
        }
    }