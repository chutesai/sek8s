"""Tests for the attestation-proxy boot-time recovery cluster-init script.

Drives ansible/guest/roles/k3s/files/cluster-init/05-attestation-proxy-recovery.sh
with a fake `kubectl` (injected via the KUBECTL env var the script honours) and
asserts the safety-critical behaviour:

  * stuck pods (phase Unknown/Failed, or reason NodeLost) are force-deleted,
  * Pending/Running pods are left untouched,
  * the script always exits 0 (k3s-cluster-init powers the VM off on non-zero),
    even when listing pods or the delete itself fails.
"""

import json
import os
import shlex
import stat
import subprocess
import sys

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
)
_SCRIPT = os.path.join(
    _REPO_ROOT,
    "ansible",
    "guest",
    "roles",
    "k3s",
    "files",
    "cluster-init",
    "05-attestation-proxy-recovery.sh",
)

# A fake kubectl: classifies argv, logs each invocation, and answers from a JSON
# state file. Behaviour is driven entirely by env so each test is hermetic.
_FAKE_KUBECTL = """\
#!/usr/bin/env python3
import json, os, shlex, sys

with open(os.environ["FAKE_STATE"]) as fh:
    state = json.load(fh)
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(shlex.join(sys.argv[1:]) + "\\n")

a = sys.argv[1:]
if a[:2] == ["get", "pods"]:
    if state.get("list_fail"):
        sys.exit(1)
    sys.stdout.write(state.get("pod_rows", ""))
    sys.exit(0)
if a and a[0] == "delete":
    sys.exit(1 if state.get("delete_fail") else 0)
sys.exit(0)
"""


def _run(tmp_path, state):
    """Run the recovery script against the fake kubectl; return (proc, calls)."""
    fake = tmp_path / "kubectl.py"
    fake.write_text(_FAKE_KUBECTL)

    # The script invokes "$KUBECTL" as a single token, so wrap the
    # "python <fake>" pair in a tiny launcher it can exec directly.
    launcher = tmp_path / "kubectl"
    launcher.write_text(f'#!/bin/sh\nexec {sys.executable} {fake} "$@"\n')
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(state))
    calls_file = tmp_path / "calls.log"
    calls_file.write_text("")

    env = {
        **os.environ,
        "KUBECTL": str(launcher),
        "FAKE_STATE": str(state_file),
        "FAKE_CALLS": str(calls_file),
    }
    proc = subprocess.run(
        ["bash", _SCRIPT], env=env, capture_output=True, text=True, timeout=30
    )
    calls = [shlex.split(line) for line in calls_file.read_text().splitlines() if line]
    return proc, calls


def _deleted_pods(calls):
    return [c[2] for c in calls if len(c) >= 3 and c[0] == "delete" and c[1] == "pod"]


def _pod_rows(*rows):
    """Build the tab/newline output the script's jsonpath query produces."""
    return "".join(f"{name}\t{phase}\t{reason}\n" for name, phase, reason in rows)


def test_force_deletes_unknown_pod(tmp_path):
    state = {"pod_rows": _pod_rows(("attestation-proxy-xtf5t", "Unknown", ""))}
    proc, calls = _run(tmp_path, state)
    assert proc.returncode == 0
    assert _deleted_pods(calls) == ["attestation-proxy-xtf5t"]


def test_force_deletes_failed_pod(tmp_path):
    state = {"pod_rows": _pod_rows(("attestation-proxy-aaa", "Failed", ""))}
    _, calls = _run(tmp_path, state)
    assert _deleted_pods(calls) == ["attestation-proxy-aaa"]


def test_force_deletes_nodelost_pod(tmp_path):
    # NodeLost is reported as a reason while phase often stays Running.
    state = {"pod_rows": _pod_rows(("attestation-proxy-bbb", "Running", "NodeLost"))}
    _, calls = _run(tmp_path, state)
    assert _deleted_pods(calls) == ["attestation-proxy-bbb"]


def test_leaves_running_pod_untouched(tmp_path):
    state = {"pod_rows": _pod_rows(("attestation-proxy-ok", "Running", ""))}
    proc, calls = _run(tmp_path, state)
    assert proc.returncode == 0
    assert _deleted_pods(calls) == []


def test_leaves_pending_pod_untouched(tmp_path):
    # A pod still in its init container (waiting on the secret) is Pending and
    # must not be killed mid-progress.
    state = {"pod_rows": _pod_rows(("attestation-proxy-init", "Pending", ""))}
    _, calls = _run(tmp_path, state)
    assert _deleted_pods(calls) == []


def test_only_stuck_pod_deleted_in_mixed_set(tmp_path):
    state = {
        "pod_rows": _pod_rows(
            ("good", "Running", ""),
            ("dead", "Unknown", ""),
            ("starting", "Pending", ""),
        )
    }
    _, calls = _run(tmp_path, state)
    assert _deleted_pods(calls) == ["dead"]


def test_no_pods_is_noop(tmp_path):
    proc, calls = _run(tmp_path, {"pod_rows": ""})
    assert proc.returncode == 0
    assert _deleted_pods(calls) == []


def test_exits_zero_when_delete_fails(tmp_path):
    state = {"delete_fail": True, "pod_rows": _pod_rows(("dead", "Unknown", ""))}
    proc, _ = _run(tmp_path, state)
    assert proc.returncode == 0


def test_exits_zero_when_pod_list_fails(tmp_path):
    state = {"list_fail": True}
    proc, calls = _run(tmp_path, state)
    assert proc.returncode == 0
    assert _deleted_pods(calls) == []
