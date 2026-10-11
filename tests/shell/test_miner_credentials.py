"""03-k3s-miner-credentials.sh writes ss58 plus exactly the one key the config volume carries.

Driven with a stub kubectl that records every call, so this covers the control flow: which key is
applied, when the other key is patched away, and that a failed lookup is retried rather than read
as "key absent". The apply/patch semantics against a real API server are covered by
tests/integration/test_miner_credentials_kind.py.
"""

import pytest

SCRIPT = "ansible/guest/roles/k3s/files/cluster-init/03-k3s-miner-credentials.sh"
NAMESPACES = ("chutes", "attestation-system")

SS58 = "5H1oTw5YnpYau6ViMURhEjS3Nar8AJc2wuzusNFbrWGpp4MP"
SEED = "30e940aa8b6ba8ff49951b7366326d60bfc64c9a7807787bca239b81009fb888"
PRIVATE_KEY = "81433c30" * 16

# `create --dry-run` prints its argv as the "manifest"; `apply` records that manifest; `get` prints
# $GET_OUTPUT, failing while the $GET_FAILURES counter file is non-empty.
KUBECTL_STUB = """
echo "$*" >> "$RECORD"
case "$1" in
  create) echo "manifest: $*" ;;
  apply) echo "applied: $(cat)" >> "$RECORD" ;;
  get)
    if [ -s "$GET_FAILURES" ]; then sed -i '1d' "$GET_FAILURES"; exit 1; fi
    printf '%s' "$GET_OUTPUT" ;;
esac
"""


@pytest.fixture
def run_03(shell):
    shell.stub("kubectl", KUBECTL_STUB)
    volume = shell.tmp / "config"
    volume.mkdir()
    record = shell.tmp / "kubectl.log"
    failures = shell.tmp / "get-failures"
    failures.write_text("")

    def _run(files, get_output="", get_failures=0):
        (volume / "miner-ss58").write_text(SS58 + "\n")
        for name, content in files.items():
            (volume / name).write_text(content + "\n")
        failures.write_text("fail\n" * get_failures)
        result = shell.run(
            SCRIPT,
            env={
                "CREDENTIALS_DIR": str(volume),
                "LOG_FILE": str(shell.tmp / "03.log"),
                "RECORD": str(record),
                "GET_OUTPUT": get_output,
                "GET_FAILURES": str(failures),
            },
        )
        calls = record.read_text().splitlines() if record.exists() else []
        return result, calls

    return _run


def _calls(calls, verb):
    return [c for c in calls if c.startswith(verb + " ")]


@pytest.mark.parametrize(
    "files, key, value, other",
    [
        ({"miner-private-key": PRIVATE_KEY}, "privateKey", PRIVATE_KEY, "seed"),
        ({"miner-seed": SEED}, "seed", SEED, "privateKey"),
    ],
    ids=["private-key", "seed"],
)
def test_applies_ss58_and_only_the_volumes_key(run_03, files, key, value, other):
    result, calls = run_03(files)

    assert result.returncode == 0, result.stderr
    creates = _calls(calls, "create")
    assert len(creates) == len(NAMESPACES)
    for create in creates:
        assert f"--from-literal=ss58={SS58}" in create
        assert f"--from-literal={key}={value}" in create
        assert f"--from-literal={other}=" not in create
    assert [c.split("-n ")[1].split()[0] for c in _calls(calls, "apply")] == list(
        NAMESPACES
    )


def test_patches_the_other_key_away_in_both_namespaces(run_03):
    """The chart's build-time secret carries seed: REPLACE_ME; a private-key VM must drop it."""
    result, calls = run_03(
        {"miner-private-key": PRIVATE_KEY}, get_output="UkVQTEFDRQ=="
    )

    assert result.returncode == 0, result.stderr
    gets = _calls(calls, "get")
    assert all("jsonpath={.data.seed}" in g for g in gets)
    patches = _calls(calls, "patch")
    assert len(patches) == len(NAMESPACES)
    for ns, patch in zip(NAMESPACES, patches):
        assert f"-n {ns} " in patch
        assert '[{"op":"remove","path":"/data/seed"}]' in patch


def test_does_not_patch_when_the_other_key_is_absent(run_03):
    """A seed-only VM that never had a private key: apply only, as before this change."""
    result, calls = run_03({"miner-seed": SEED}, get_output="")

    assert result.returncode == 0, result.stderr
    assert _calls(calls, "patch") == []


def test_a_failed_lookup_is_retried_not_read_as_absent(run_03):
    """retry_kubectl runs the step as an `until` condition, where set -e does not apply."""
    result, calls = run_03(
        {"miner-private-key": PRIVATE_KEY}, get_output="UkVQTEFDRQ==", get_failures=1
    )

    assert result.returncode == 0, result.stderr
    assert len(_calls(calls, "patch")) == len(NAMESPACES)


@pytest.mark.parametrize(
    "files",
    [{}, {"miner-seed": SEED, "miner-private-key": PRIVATE_KEY}],
    ids=["no-key", "both-keys"],
)
def test_refuses_a_volume_without_exactly_one_key(run_03, files):
    result, calls = run_03(files)

    assert result.returncode != 0
    assert calls == []
