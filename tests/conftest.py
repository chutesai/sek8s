import os

from fixtures.env import *  # noqa


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

    # Print confirmation for debugging
    print("Environment variables set up for testing!")


pytest_configure(None)

from fixtures.k8s import *  # noqa
from fixtures.http import *  # noqa
