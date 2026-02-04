import os
from unittest.mock import patch

import pytest

from fixtures.env import *  # noqa


def _noop_cached(**kwargs):
    """No-op replacement for aiocache.cached so cached endpoints don't leak between tests."""

    def decorator(f):
        return f

    return decorator


@pytest.fixture(scope="session", autouse=True)
def disable_aiocache():
    """
    Mock aiocache.cached for the test run so cached endpoints don't leak between tests.
    Must run before the status router is imported. Therefore create_app() and the status
    router must only be imported inside fixtures (e.g. status_client) or test bodies,
    never at test module top level.
    """
    with patch("aiocache.cached", _noop_cached):
        yield


@pytest.fixture
def manager_app_no_auth():
    """Create the system-manager app with miner auth bypassed for testing."""
    def _noop_authorize(*args, **kwargs):
        def _dep():
            return None

        return _dep

    with patch("sek8s.services.util.authorize", side_effect=_noop_authorize):
        from sek8s.services.manager import create_app

        yield create_app()


def pytest_configure(config):
    """Set up environment variables before any modules are imported."""
    os.environ["MINER_SS58"] = "5E6xfU3oNU7y1a7pQwoc31fmUjwBZ2gKcNCw8EXsdtCQieUQ"
    os.environ["MINER_SEED"] = "0xe031170f32b4cda05df2f3cf6bc8d7687b683bbce23d9fa960c0b3fc21641b8a"

    os.environ["PATH"] = f"{os.environ['PATH']}:./bin"

    os.environ["POLICY_PATH"] = os.path.join(os.getcwd(), "opa/policies")

    os.environ["ALLOWED_VALIDATORS"] = "5E6xfU3oNU7y1a7pQwoc31fmUjwBZ2gKcNCw8EXsdtCQieUQ,5DAAnrj7VHTz5kZ8Yx9T6UzU6Fv5fV8qD5T4v4k1zX7N6P4Y"

    os.environ.setdefault("DEBUG", "false")
    os.environ.setdefault("REGISTRY_URL", "localhost:5000")
    os.environ.setdefault("COSIGN_PASSWORD", "testpassword")

    # Cache config (used by sek8s.config.cache_config); tests mock via env
    os.environ.setdefault("HF_CACHE_BASE", os.path.join(os.getcwd(), "tests", "tmp", "cache"))
    os.environ.setdefault("VALIDATOR_BASE_URL", "https://api.chutes.ai")

    # Print confirmation for debugging
    print("Environment variables set up for testing!")


pytest_configure(None)

from fixtures.k8s import *  # noqa
from fixtures.http import *  # noqa
