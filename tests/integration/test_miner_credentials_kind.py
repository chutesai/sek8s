"""03-k3s-miner-credentials.sh against a real API server: the secret ends up holding ss58 plus
exactly the config volume's key, starting from the chart-created secret or from a previous key.

The chart creates miner-credentials at image build (ss58/seed = REPLACE_ME) without kubectl's
last-applied annotation, so `kubectl apply` alone would never prune the build-time seed. A stubbed
kubectl cannot show that, hence a real cluster.

Opt-in and throwaway only: set SEK8S_KIND_KUBECONFIG to the kubeconfig of a kind cluster, e.g.

    kind create cluster --name sek8s-test --kubeconfig /tmp/sek8s-kind.yaml
    SEK8S_KIND_KUBECONFIG=/tmp/sek8s-kind.yaml pytest tests/integration/test_miner_credentials_kind.py

The test refuses any context not named kind-*, since it writes into the chutes and
attestation-system namespaces.
"""

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ansible/guest/roles/k3s/files/cluster-init/03-k3s-miner-credentials.sh"
NAMESPACES = ("chutes", "attestation-system")
KUBECONFIG = os.environ.get("SEK8S_KIND_KUBECONFIG", "")

# Throwaway Bittensor 11 hotkey (tests/rust/fixtures/hotkey-bittensor11-full.json).
SS58 = "5H1oTw5YnpYau6ViMURhEjS3Nar8AJc2wuzusNFbrWGpp4MP"
SEED = "30e940aa8b6ba8ff49951b7366326d60bfc64c9a7807787bca239b81009fb888"
PRIVATE_KEY = (
    "81433c30f96e2c3ca96165036d65d9deffa6fcc7eafdfa029162026abe91cc0c"
    "7acd542677534ebc6a8eddd23d6edbb8e6a4afadd5deccbf836ed5ba8229bf11"
)
# chutes-gpu/tasks/setup_chutes.yml installs the chart with these at image build.
BUILD_SS58 = "REPLACE_WITH_ANSIBLE_SS58_ADDRESS"
BUILD_SEED = "REPLACE_WITH_ANSIBLE_SECRET_SEED"

pytestmark = pytest.mark.skipif(
    not KUBECONFIG or shutil.which("kubectl") is None,
    reason="set SEK8S_KIND_KUBECONFIG to a throwaway kind cluster to run",
)


def kubectl(*args, stdin=None, check=True):
    result = subprocess.run(
        ["kubectl", f"--kubeconfig={KUBECONFIG}", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"kubectl {args} failed: {result.stderr}")
    return result.stdout


@pytest.fixture(scope="module", autouse=True)
def kind_cluster():
    context = kubectl("config", "current-context").strip()
    if not context.startswith("kind-"):
        pytest.fail(f"refusing to run against context {context!r}: not a kind cluster")
    for ns in NAMESPACES:
        kubectl("create", "namespace", ns, check=False)
    yield
    for ns in NAMESPACES:
        kubectl("delete", "namespace", ns, "--wait=false", check=False)


@pytest.fixture(autouse=True)
def no_secret():
    for ns in NAMESPACES:
        kubectl("delete", "secret", "miner-credentials", "-n", ns, "--ignore-not-found")


def chart_created_secret():
    """The secret as the image build leaves it: created, not applied."""
    for ns in NAMESPACES:
        kubectl(
            "create",
            "secret",
            "generic",
            "miner-credentials",
            "-n",
            ns,
            f"--from-literal=ss58={BUILD_SS58}",
            f"--from-literal=seed={BUILD_SEED}",
        )


def run_03(tmp_path, **keys):
    volume = tmp_path / "config"
    shutil.rmtree(volume, ignore_errors=True)
    volume.mkdir()
    (volume / "miner-ss58").write_text(SS58 + "\n")
    for name, value in keys.items():
        (volume / name.replace("_", "-")).write_text(value + "\n")
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "KUBECONFIG": KUBECONFIG,
            "CREDENTIALS_DIR": str(volume),
            "LOG_FILE": str(tmp_path / "03.log"),
        },
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def secret_data(ns):
    secret = json.loads(
        kubectl("get", "secret", "miner-credentials", "-n", ns, "-o", "json")
    )
    return {k: base64.b64decode(v).decode() for k, v in secret["data"].items()}


def assert_secret(expected):
    for ns in NAMESPACES:
        assert secret_data(ns) == expected, ns


def test_apply_alone_keeps_the_build_time_seed():
    """Control for the tests below: why 03 removes the other key explicitly."""
    chart_created_secret()
    manifest = kubectl(
        "create",
        "secret",
        "generic",
        "miner-credentials",
        f"--from-literal=ss58={SS58}",
        f"--from-literal=privateKey={PRIVATE_KEY}",
        "--dry-run=client",
        "-o",
        "yaml",
    )
    kubectl("apply", "-n", "chutes", "-f", "-", stdin=manifest)
    assert secret_data("chutes")["seed"] == BUILD_SEED


def test_fresh_private_key_vm(tmp_path):
    chart_created_secret()
    run_03(tmp_path, miner_private_key=PRIVATE_KEY)
    assert_secret({"ss58": SS58, "privateKey": PRIVATE_KEY})


def test_fresh_seed_vm_and_reboot_are_unchanged(tmp_path):
    chart_created_secret()
    run_03(tmp_path, miner_seed=SEED)
    assert_secret({"ss58": SS58, "seed": SEED})
    versions = [
        kubectl(
            "get",
            "secret",
            "miner-credentials",
            "-n",
            ns,
            "-o",
            "jsonpath={.metadata.resourceVersion}",
        )
        for ns in NAMESPACES
    ]

    run_03(tmp_path, miner_seed=SEED)

    assert_secret({"ss58": SS58, "seed": SEED})
    for ns, version in zip(NAMESPACES, versions):
        assert (
            kubectl(
                "get",
                "secret",
                "miner-credentials",
                "-n",
                ns,
                "-o",
                "jsonpath={.metadata.resourceVersion}",
            )
            == version
        ), f"{ns}: a seed-only reboot rewrote the secret"


def test_existing_vm_switches_from_seed_to_private_key(tmp_path):
    chart_created_secret()
    run_03(tmp_path, miner_seed=SEED)
    run_03(tmp_path, miner_private_key=PRIVATE_KEY)
    assert_secret({"ss58": SS58, "privateKey": PRIVATE_KEY})


def test_existing_vm_switches_from_private_key_to_seed(tmp_path):
    chart_created_secret()
    run_03(tmp_path, miner_private_key=PRIVATE_KEY)
    run_03(tmp_path, miner_seed=SEED)
    assert_secret({"ss58": SS58, "seed": SEED})
