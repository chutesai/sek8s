# tests/fixtures/k8s.py
"""
Shared test fixtures for TEE Admission Controller tests
"""

import os
import pytest
from unittest.mock import Mock, AsyncMock
from pathlib import Path

from sek8s.services.admission_controller import AdmissionController, AdmissionWebhookServer
from sek8s.config import AdmissionConfig, NamespacePolicy
from sek8s.validators.base import ValidationResult


@pytest.fixture
def test_config():
    """Create test configuration using Pydantic."""
    config = AdmissionConfig(
        bind_address="127.0.0.1",
        port=8443,
        opa_url="http://localhost:8181",
        opa_timeout=5.0,
        policy_path=Path("./opa/policies"),
        allowed_registries=["docker.io", "gcr.io", "quay.io", "localhost:30500"],
        cache_enabled=True,
        cache_ttl=300,
        enforcement_mode="enforce",
        namespace_policies={
            "kube-system": NamespacePolicy(mode="warn", exempt=False),
            "default": NamespacePolicy(mode="enforce", exempt=False),
            "test-exempt": NamespacePolicy(mode="monitor", exempt=True),
        },
        debug=True,
        metrics_enabled=True
    )
    return config


@pytest.fixture
def admission_controller(test_config):
    """Create admission controller instance for testing."""
    return AdmissionController(test_config)


@pytest.fixture
def webhook_server(test_config):
    """Create webhook server instance for testing."""
    return AdmissionWebhookServer(test_config)


@pytest.fixture
def valid_admission_review():
    """Create a valid admission review request."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-123",
            "operation": "CREATE",
            "namespace": "default",
            "name": "test-pod",
            "kind": {
                "kind": "Pod",
                "version": "v1",
                "group": ""
            },
            "object": {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": "test-pod",
                    "namespace": "default"
                },
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "docker.io/library/nginx:latest",
                            "resources": {
                                "limits": {
                                    "memory": "256Mi",
                                    "cpu": "500m"
                                }
                            }
                        }
                    ]
                }
            }
        }
    }


@pytest.fixture
def privileged_pod_review():
    """Create admission review with privileged pod."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-456",
            "operation": "CREATE",
            "namespace": "default",
            "kind": {
                "kind": "Pod",
                "version": "v1"
            },
            "object": {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": "privileged-pod",
                    "namespace": "default"
                },
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "docker.io/library/nginx:latest",
                            "securityContext": {
                                "privileged": True
                            },
                            "resources": {
                                "limits": {
                                    "memory": "256Mi"
                                }
                            }
                        }
                    ]
                }
            }
        }
    }


@pytest.fixture
def host_network_pod_review():
    """Create admission review with host network pod."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-host-net",
            "operation": "CREATE",
            "namespace": "default",
            "kind": {
                "kind": "Pod",
                "version": "v1"
            },
            "object": {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": "host-network-pod",
                    "namespace": "default"
                },
                "spec": {
                    "hostNetwork": True,
                    "containers": [
                        {
                            "name": "app",
                            "image": "docker.io/library/nginx:latest",
                            "resources": {
                                "limits": {
                                    "memory": "256Mi"
                                }
                            }
                        }
                    ]
                }
            }
        }
    }


@pytest.fixture
def untrusted_registry_review():
    """Create admission review with untrusted registry."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-789",
            "operation": "CREATE",
            "namespace": "default",
            "kind": {
                "kind": "Pod",
                "version": "v1"
            },
            "object": {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": "untrusted-pod",
                    "namespace": "default"
                },
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "untrusted-registry.com/malicious:latest",
                            "resources": {
                                "limits": {
                                    "memory": "256Mi"
                                }
                            }
                        }
                    ]
                }
            }
        }
    }


@pytest.fixture
def no_limits_pod_review():
    """Create admission review with pod missing resource limits."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-no-limits",
            "operation": "CREATE",
            "namespace": "default",
            "kind": {
                "kind": "Pod",
                "version": "v1"
            },
            "object": {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": "no-limits-pod",
                    "namespace": "default"
                },
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "docker.io/library/nginx:latest"
                            # Missing resources.limits
                        }
                    ]
                }
            }
        }
    }


@pytest.fixture
def invalid_hostpath_review():
    """Create admission review with invalid hostPath volume."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-hostpath",
            "operation": "CREATE",
            "namespace": "default",
            "kind": {
                "kind": "Pod",
                "version": "v1"
            },
            "object": {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": "hostpath-pod",
                    "namespace": "default"
                },
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "docker.io/library/nginx:latest",
                            "resources": {
                                "limits": {
                                    "memory": "256Mi"
                                }
                            },
                            "volumeMounts": [
                                {
                                    "name": "host-etc",
                                    "mountPath": "/host-etc"
                                }
                            ]
                        }
                    ],
                    "volumes": [
                        {
                            "name": "host-etc",
                            "hostPath": {
                                "path": "/etc",  # Not allowed (should be /cache/*)
                                "type": "Directory"
                            }
                        }
                    ]
                }
            }
        }
    }


@pytest.fixture
def valid_cache_mount_review():
    """Create admission review with valid /cache hostPath."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-cache",
            "operation": "CREATE",
            "namespace": "default",
            "kind": {
                "kind": "Pod",
                "version": "v1"
            },
            "object": {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": "cache-pod",
                    "namespace": "default"
                },
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "docker.io/library/nginx:latest",
                            "resources": {
                                "limits": {
                                    "memory": "256Mi",
                                    "cpu": "500m"
                                }
                            },
                            "volumeMounts": [
                                {
                                    "name": "cache",
                                    "mountPath": "/data"
                                }
                            ]
                        }
                    ],
                    "volumes": [
                        {
                            "name": "cache",
                            "hostPath": {
                                "path": "/cache/app-data",
                                "type": "DirectoryOrCreate"
                            }
                        }
                    ]
                }
            }
        }
    }


@pytest.fixture
def deployment_review():
    """Create admission review for deployment."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-deployment",
            "operation": "CREATE",
            "namespace": "default",
            "kind": {
                "kind": "Deployment",
                "version": "v1",
                "group": "apps"
            },
            "object": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": "test-deployment",
                    "namespace": "default"
                },
                "spec": {
                    "replicas": 3,
                    "template": {
                        "metadata": {
                            "labels": {
                                "app": "test"
                            }
                        },
                        "spec": {
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "docker.io/library/nginx:latest",
                                    "resources": {
                                        "limits": {
                                            "memory": "256Mi",
                                            "cpu": "500m"
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }
    }


@pytest.fixture
def cronjob_review():
    """Create admission review for CronJob."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-cronjob",
            "operation": "CREATE",
            "namespace": "default",
            "kind": {
                "kind": "CronJob",
                "version": "v1",
                "group": "batch"
            },
            "object": {
                "apiVersion": "batch/v1",
                "kind": "CronJob",
                "metadata": {
                    "name": "test-cronjob",
                    "namespace": "default"
                },
                "spec": {
                    "schedule": "*/5 * * * *",
                    "jobTemplate": {
                        "spec": {
                            "template": {
                                "spec": {
                                    "containers": [
                                        {
                                            "name": "worker",
                                            "image": "docker.io/library/busybox:latest",
                                            "command": ["echo", "hello"],
                                            "resources": {
                                                "limits": {
                                                    "memory": "128Mi",
                                                    "cpu": "100m"
                                                }
                                            }
                                        }
                                    ],
                                    "restartPolicy": "OnFailure"
                                }
                            }
                        }
                    }
                }
            }
        }
    }


@pytest.fixture
def namespace_creation_review():
    """Create admission review for namespace creation (should be blocked)."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-namespace",
            "operation": "CREATE",
            "kind": {
                "kind": "Namespace",
                "version": "v1",
                "group": ""
            },
            "object": {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": "unauthorized-namespace"
                }
            }
        }
    }


@pytest.fixture
def service_review():
    """Create admission review for service (should be allowed)."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-service",
            "operation": "CREATE",
            "namespace": "default",
            "kind": {
                "kind": "Service",
                "version": "v1",
                "group": ""
            },
            "object": {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {
                    "name": "test-service",
                    "namespace": "default"
                },
                "spec": {
                    "selector": {
                        "app": "test"
                    },
                    "ports": [
                        {
                            "port": 80,
                            "targetPort": 8080
                        }
                    ]
                }
            }
        }
    }


@pytest.fixture
def exempt_namespace_review():
    """Create admission review for exempt namespace."""
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "request": {
            "uid": "test-uid-exempt",
            "operation": "CREATE",
            "namespace": "test-exempt",
            "kind": {
                "kind": "Pod",
                "version": "v1"
            },
            "object": {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": "exempt-pod",
                    "namespace": "test-exempt"
                },
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "untrusted-registry.com/app:latest",  # Should be allowed in exempt namespace
                            "securityContext": {
                                "privileged": True  # Should be allowed in exempt namespace
                            }
                        }
                    ]
                }
            }
        }
    }


@pytest.fixture
def mock_opa_validator():
    """Create a mock OPA validator."""
    validator = AsyncMock()
    validator.validate = AsyncMock(return_value=ValidationResult.allow())
    validator.health_check = AsyncMock(return_value=True)
    return validator


@pytest.fixture
def mock_registry_validator():
    """Create a mock registry validator."""
    validator = AsyncMock()
    validator.validate = AsyncMock(return_value=ValidationResult.allow())
    validator.health_check = AsyncMock(return_value=True)
    return validator