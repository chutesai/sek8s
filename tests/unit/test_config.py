import os
import pytest
from sek8s.config import Config, AdmissionSettings, OPAEngineSettings


# Helper function to clear environment variables
def clear_test_env_vars():
    """Clear all test-related environment variables."""
    test_vars = [
        'TLS_CERT_FILE', 'TLS_PRIVATE_KEY_FILE', 'ALLOWED_REGISTRIES', 'CONTROLLER_PORT',
        'OPA_PORT', 'POLICY_DIR', 'DEBUG'
    ]
    for var in test_vars:
        os.environ.pop(var, None)


# Base Config class tests
def test_config_base_class_cannot_be_instantiated_without_properties():
    """Test that base Config class works when subclassed."""
    class TestConfig(Config):
        test_field: str
    
    clear_test_env_vars()
    with pytest.raises(RuntimeError, match="Required environment variable 'TEST_FIELD' is not set"):
        TestConfig()


def test_config_unsupported_type_raises_error():
    """Test that unsupported types raise RuntimeError."""
    class TestConfig(Config):
        unsupported_field: dict  # Unsupported type
    
    clear_test_env_vars()
    os.environ['UNSUPPORTED_FIELD'] = 'some_value'
    
    with pytest.raises(RuntimeError, match="Unsupported type for configuration value"):
        TestConfig()


# AdmissionSettings tests
def test_admission_settings_with_all_env_vars():
    """Test AdmissionSettings when all environment variables are set."""
    clear_test_env_vars()
    os.environ.update({
        'TLS_CERT_FILE': '/path/to/cert.pem',
        'TLS_PRIVATE_KEY_FILE': '/path/to/key.pem',
        'CONTROLLER_PORT': '9999'
    })
    
    config = AdmissionSettings()
    
    assert config.tls_cert_file == '/path/to/cert.pem'
    assert config.tls_private_key_file == '/path/to/key.pem'
    assert config.controller_port == 9999


def test_admission_settings_with_optional_fields_none():
    """Test AdmissionSettings when optional fields are not set."""
    clear_test_env_vars()
    
    config = AdmissionSettings()
    
    assert config.tls_cert_file is None
    assert config.tls_private_key_file is None
    assert config.controller_port == 8884  # Default value


def test_admission_settings_with_default_port():
    """Test AdmissionSettings uses default port when not specified."""
    clear_test_env_vars()
    os.environ['ALLOWED_REGISTRIES'] = 'docker.io'
    
    config = AdmissionSettings()
    
    assert config.controller_port == 8884


def test_opa_settings_missing_required_field():
    """Test AdmissionSettings raises error when required field is missing."""
    clear_test_env_vars()
    os.environ['POLICY_DIR'] = '/tmp/policies'
    # Don't set ALLOWED_REGISTRIES
    
    with pytest.raises(RuntimeError, match="Required environment variable 'ALLOWED_REGISTRIES' is not set"):
        OPAEngineSettings()


def test_admission_settings_invalid_port():
    """Test AdmissionSettings raises error for invalid port value."""
    clear_test_env_vars()
    os.environ.update({
        'ALLOWED_REGISTRIES': 'docker.io',
        'CONTROLLER_PORT': 'invalid_port'
    })
    
    with pytest.raises(RuntimeError, match="Invalid integer value for 'CONTROLLER_PORT'"):
        AdmissionSettings()


def test_opa_settings_empty_registries():
    """Test AdmissionSettings handles empty registries list."""
    clear_test_env_vars()
    os.environ.update({
        'ALLOWED_REGISTRIES': '   ,  ,   ',  # Only whitespace and commas
        'POLICY_DIR': '/tmp/policies'
    })
    
    config = OPAEngineSettings()
    
    assert config.allowed_registries == []


def test_opa_settings_registries_with_whitespace():
    """Test AdmissionSettings properly trims whitespace from registries."""
    clear_test_env_vars()
    os.environ.update({
        'ALLOWED_REGISTRIES': ' docker.io , quay.io  ,  gcr.io ',
        'POLICY_DIR': '/tmp/policies'
    })
    
    config = OPAEngineSettings()
    
    assert config.allowed_registries == ['docker.io', 'quay.io', 'gcr.io']


# OPAEngineSettings tests
def test_opa_engine_settings_with_all_env_vars():
    """Test OPAEngineSettings when all environment variables are set."""
    clear_test_env_vars()
    os.environ.update({
        'POLICY_DIR': '/etc/policies',
        'ALLOWED_REGISTRIES': 'docker.io,quay.io,gcr.io',
        'DEBUG': 'true'
    })
    
    config = OPAEngineSettings()
    
    assert config.policy_dir == '/etc/policies'
    assert config.debug is True
    assert config.allowed_registries == ['docker.io', 'quay.io', 'gcr.io']


def test_opa_engine_settings_missing_required_field():
    """Test OPAEngineSettings raises error when required field is missing."""
    clear_test_env_vars()
    # Don't set POLICY_DIR
    
    with pytest.raises(RuntimeError, match="Required environment variable 'POLICY_DIR' is not set"):
        OPAEngineSettings()

def test_opa_engine_settings_boolean_values():
    """Test OPAEngineSettings handles various boolean values correctly."""
    clear_test_env_vars()
    os.environ.update({
        'POLICY_DIR': '/etc/policies',
        'ALLOWED_REGISTRIES': 'docker.io,quay.io,gcr.io'
    })
    
    # Test true values
    for true_val in ['true', 'True', 'TRUE', '1', 'yes', 'YES', 'on', 'ON']:
        os.environ['DEBUG'] = true_val
        config = OPAEngineSettings()
        assert config.debug is True, f"Failed for true value: {true_val}"
    
    # Test false values
    for false_val in ['false', 'False', 'FALSE', '0', 'no', 'NO', 'off', 'OFF']:
        os.environ['DEBUG'] = false_val
        config = OPAEngineSettings()
        assert config.debug is False, f"Failed for false value: {false_val}"


def test_opa_engine_settings_invalid_boolean():
    """Test OPAEngineSettings raises error for invalid boolean value."""
    clear_test_env_vars()
    os.environ.update({
        'POLICY_DIR': '/etc/policies',
        'ALLOWED_REGISTRIES': 'docker.io,quay.io,gcr.io',
        'DEBUG': 'maybe'
    })
    
    with pytest.raises(RuntimeError, match="Invalid boolean value for 'DEBUG'"):
        OPAEngineSettings()


def test_opa_engine_settings_default_debug_false():
    """Test OPAEngineSettings sets debug to None when not provided."""
    clear_test_env_vars()
    os.environ.update({
        'POLICY_DIR': '/etc/policies',
        'ALLOWED_REGISTRIES': 'docker.io,quay.io,gcr.io'
    })
    
    config = OPAEngineSettings()
    
    assert config.debug is False


# Integration tests
def test_multiple_config_classes_independent():
    """Test that multiple config classes work independently."""
    clear_test_env_vars()
    
    # Set env vars for both configs
    os.environ.update({
        'ALLOWED_REGISTRIES': 'docker.io',
        'CONTROLLER_PORT': '9000',
        'POLICY_DIR': '/etc/policies',
        'DEBUG': 'true'
    })
    
    admission_config = AdmissionSettings()
    opa_config = OPAEngineSettings()
    
    # Verify each config only has its own fields
    assert admission_config.controller_port == 9000
    assert admission_config.debug is True
    assert admission_config.tls_cert_file is None
    assert admission_config.tls_private_key_file is None
    assert not hasattr(admission_config, 'policy_dir')
    assert not hasattr(admission_config, 'allowed_registries')
    
    assert opa_config.policy_dir == '/etc/policies'
    assert opa_config.allowed_registries == ['docker.io']
    assert opa_config.debug is True
    assert not hasattr(opa_config, 'controller_port')


def test_config_inheritance():
    """Test that config classes properly inherit from Config base class."""
    clear_test_env_vars()
    os.environ.update({
        'ALLOWED_REGISTRIES': 'docker.io',
        'POLICY_DIR': '/etc/policies'
    })
    
    admission_config = AdmissionSettings()
    opa_config = OPAEngineSettings()
    
    assert isinstance(admission_config, Config)
    assert isinstance(opa_config, Config)
    assert hasattr(admission_config, '_load')
    assert hasattr(opa_config, '_load')


# Cleanup after tests
def test_cleanup():
    """Cleanup environment variables after all tests."""
    clear_test_env_vars()