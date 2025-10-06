import os
import pytest


@pytest.fixture(autouse=True, scope="module")
def env():
    original_env = os.environ.copy()

    yield

    os.environ.clear()
    os.environ.update(original_env)
