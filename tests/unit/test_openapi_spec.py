"""Validate that the manager app generates a valid OpenAPI 3.x spec."""

import os

import pytest

# Ensure config can load (optional env for validator)
os.environ.setdefault("VALIDATOR_BASE_URL", "https://api.example.com")


def test_manager_openapi_schema_is_valid():
    """Generate OpenAPI schema from manager app and validate it."""
    from sek8s.services.manager import create_app

    app = create_app()

    schema = app.openapi()
    assert "openapi" in schema
    assert schema.get("openapi", "").startswith("3.")
    assert "paths" in schema

    # Must be JSON-serializable (no Path or other non-serializable types)
    import json
    json_str = json.dumps(schema)
    assert len(json_str) > 0

    # Validate with openapi-spec-validator if available
    try:
        from openapi_spec_validator import validate_spec
        validate_spec(schema)
    except ImportError:
        pytest.skip("openapi-spec-validator not installed")
