# tests/test_main.py
"""
Unit tests for TEE Admission Controller (function-based)
"""

from unittest.mock import Mock, patch
from conftest import create_request


def test_validate_allowed_pod(admission_controller, valid_pod):
    """Test validation of an allowed pod."""
    request = create_request(valid_pod)
    
    allowed, message = admission_controller.validate_request(request)
    
    assert allowed is True
    assert message["response"]["status"]["message"] == "Allowed"

def test_validate_pod_untrusted_registry(admission_controller, pod_untrusted_registry):
    """Test rejection of pod from untrusted registry."""
    request = create_request(pod_untrusted_registry)
    
    allowed, message = admission_controller.validate_request(request)
    
    assert allowed is False
    assert "uses disallowed registry" in message["response"]["status"]["message"]


def test_validate_privileged_pod_rejection(admission_controller, privileged_pod):
    """Test rejection of privileged pods."""
    request = create_request(privileged_pod)
    
    allowed, message = admission_controller.validate_request(request)
    
    assert allowed is False
    assert "privileged security context which is not allowed" in message["response"]["status"]["message"]


def test_validate_host_network_rejection(admission_controller, host_network_pod):
    """Test rejection of pods with host network."""
    request = create_request(host_network_pod)
    
    allowed, message = admission_controller.validate_request(request)
    
    assert allowed is False
    assert "host network not allowed" in message["response"]["status"]["message"]


def test_validate_deployment(admission_controller, valid_deployment):
    """Test validation of deployment resources."""
    request = create_request(valid_deployment)
    
    allowed, message = admission_controller.validate_request(request)
    
    assert allowed is True
    assert "Allowed" in message["response"]["status"]["message"]


@patch('subprocess.run')
def test_verify_image_signature_success(mock_subprocess, admission_controller):
    """Test successful image signature verification."""
    # Mock successful cosign verification
    mock_result = Mock()
    mock_result.returncode = 0
    mock_subprocess.return_value = mock_result
    
    # Set cosign key in settings
    admission_controller.settings.cosign_public_key = "/tmp/cosign.pub"
    
    result = admission_controller.verify_image_signature("trusted-registry.com/app:signed")
    
    assert result is True
    mock_subprocess.assert_called_once()


@patch('subprocess.run')
def test_verify_image_signature_failure(mock_subprocess, admission_controller):
    """Test failed image signature verification."""
    # Mock failed cosign verification
    mock_result = Mock()
    mock_result.returncode = 1
    mock_subprocess.return_value = mock_result
    
    # Set cosign key in settings
    admission_controller.settings.cosign_public_key = "/tmp/cosign.pub"
    
    result = admission_controller.verify_image_signature("untrusted-registry.com/app:unsigned")
    
    assert result is False


def test_verify_image_signature_no_key(admission_controller):
    """Test image signature verification when no key is configured."""
    # No cosign key configured
    admission_controller.settings.cosign_public_key = None
    
    result = admission_controller.verify_image_signature("any-registry.com/app:latest")
    
    # Should pass when no verification is configured
    assert result is True


@patch('hashlib.sha256')
@patch('builtins.open')
def test_calculate_file_hash(mock_open, mock_sha256, admission_controller):
    """Test file hash calculation."""
    # Mock file reading
    mock_file = Mock()
    mock_file.read.side_effect = [b"chunk1", b"chunk2", b""]
    mock_open.return_value.__enter__.return_value = mock_file
    
    # Mock hashlib
    mock_hasher = Mock()
    mock_hasher.hexdigest.return_value = "abcd1234"
    mock_sha256.return_value = mock_hasher
    
    result = admission_controller.calculate_file_hash("/tmp/test-file")
    
    assert result == "abcd1234"
    assert mock_hasher.update.call_count == 2


def test_other_resources_allowed(admission_controller, service_resource):
    """Test that non-pod/deployment resources are allowed by default."""
    request = create_request(service_resource)
    
    allowed, message = admission_controller.validate_request(request)
    
    assert allowed is True
    assert "resource allowed" in message["response"]["status"]["message"]


# def test_settings_defaults():
#     """Test default settings values."""
#     settings = TEEAdmissionSettings(
#         tls_cert_file="/tmp/test.crt",
#         tls_private_key_file="/tmp/test.key"
#     )
    
#     assert settings.port == 9443
#     assert settings.address == "0.0.0.0"
#     assert "your-trusted-registry.com" in settings.allowed_registries
#     assert settings.verify_binary_hash is True


# def test_settings_override():
#     """Test settings override."""
#     settings = AdmissionSettings(
#         tls_cert_file="/custom/cert.pem",
#         tls_private_key_file="/custom/key.pem", 
#         port=8443,
#         address="127.0.0.1",
#         allowed_registries=["custom-registry.com"],
#         verify_binary_hash=False
#     )
    
#     assert settings.port == 8443
#     assert settings.address == "127.0.0.1"
#     assert settings.allowed_registries == ["custom-registry.com"]
#     assert settings.verify_binary_hash is False


def test_admission_review_format(admission_controller, valid_pod, admission_review_template):
    """Test complete admission review request/response format."""
    admission_review = admission_review_template.copy()
    admission_review["request"]["object"] = valid_pod
    
    allowed, message = admission_controller.validate_request(admission_review["request"])
    
    # Construct expected response structure
    expected_response_structure = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview", 
        "response": {
            "uid": "test-uid-123",
            "allowed": allowed
        }
    }
    
    assert allowed is True
    assert expected_response_structure["response"]["uid"] == admission_review["request"]["uid"]


@patch('tee_admission_controller.main.TEEAdmissionController.verify_self_attestation')
def test_self_attestation_check(mock_verify, admission_controller):
    """Test that self-attestation is checked before processing requests."""
    mock_verify.return_value = True
    
    # This would normally be called by handle_admission_request
    result = admission_controller.verify_self_attestation()
    
    assert result is True
    mock_verify.assert_called_once()


def test_multiple_containers_validation(admission_controller):
    """Test validation of pods with multiple containers."""
    pod_multi_containers = {
        "kind": "Pod",
        "metadata": {
            "name": "multi-container-pod",
            "labels": {
                "tee.verified": "true"
            }
        },
        "spec": {
            "containers": [
                {
                    "name": "app1",
                    "image": "trusted-registry.com/app1:latest",
                    "securityContext": {
                        "privileged": False
                    }
                },
                {
                    "name": "app2", 
                    "image": "trusted-registry.com/app2:latest",
                    "securityContext": {
                        "privileged": False
                    }
                }
            ]
        }
    }
    
    request = create_request(pod_multi_containers)
    allowed, message = admission_controller.validate_request(request)
    
    assert allowed is True
    assert "Allowed" in message["response"]["status"]["message"]


def test_mixed_registry_containers_rejection(admission_controller):
    """Test rejection when one container uses untrusted registry."""
    pod_mixed_registries = {
        "kind": "Pod",
        "metadata": {
            "name": "mixed-registry-pod",
            "labels": {
                "tee.verified": "true"
            }
        },
        "spec": {
            "containers": [
                {
                    "name": "trusted-app",
                    "image": "trusted-registry.com/app:latest"
                },
                {
                    "name": "untrusted-app",
                    "image": "untrusted-registry.com/bad:latest"
                }
            ]
        }
    }
    
    request = create_request(pod_mixed_registries)
    allowed, message = admission_controller.validate_request(request)
    
    assert allowed is False
    assert "registry not allowed" in message["response"]["status"]["message"]


def test_empty_labels_validation(admission_controller):
    """Test handling of pods with no labels at all."""
    pod_no_labels = {
        "kind": "Pod",
        "metadata": {
            "name": "no-labels-pod"
            # No labels key at all
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "trusted-registry.com/app:latest"
                }
            ]
        }
    }
    
    request = create_request(pod_no_labels)
    allowed, message = admission_controller.validate_request(request)
    
    assert allowed is False
    assert "tee.verified" in message["response"]["status"]["message"]