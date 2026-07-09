"""Tests for 98-clear-terminal-pods.sh selection logic.

It must delete only stale, controller-owned (ReplicaSet/DaemonSet/StatefulSet) pods:
terminal Failed/Succeeded tombstones (graceful delete) plus stuck Unknown / NodeLost
pods (force delete). It must KEEP healthy Running/Pending pods, Job/CronJob pods,
operator one-shots (owner kind not RS/DS/SS), and bare pods. Driven with a stub
kubectl that records delete calls.
"""

import json

SCRIPT = "ansible/guest/roles/k3s/files/cluster-init/98-clear-terminal-pods.sh"

PODS = {
    "items": [
        {
            "metadata": {
                "namespace": "kube-system",
                "name": "attproxy-nodelost",
                "ownerReferences": [{"controller": True, "kind": "DaemonSet"}],
            },
            "status": {"phase": "Running", "reason": "NodeLost"},
        },
        {
            "metadata": {
                "namespace": "kube-system",
                "name": "attproxy-unknown",
                "ownerReferences": [{"controller": True, "kind": "DaemonSet"}],
            },
            "status": {"phase": "Unknown"},
        },
        {
            "metadata": {
                "namespace": "chutes",
                "name": "agent-failed",
                "ownerReferences": [{"controller": True, "kind": "ReplicaSet"}],
            },
            "status": {"phase": "Failed"},
        },
        {
            "metadata": {
                "namespace": "chutes",
                "name": "agent-running",
                "ownerReferences": [{"controller": True, "kind": "ReplicaSet"}],
            },
            "status": {"phase": "Running"},
        },
        {
            "metadata": {
                "namespace": "chutes",
                "name": "job-succeeded",
                "ownerReferences": [{"controller": True, "kind": "Job"}],
            },
            "status": {"phase": "Succeeded"},
        },
        {
            "metadata": {"namespace": "default", "name": "bare-unknown"},
            "status": {"phase": "Unknown"},
        },
        {
            "metadata": {
                "namespace": "gpu",
                "name": "cuda-validator",
                "ownerReferences": [{"controller": True, "kind": "Pod"}],
            },
            "status": {"phase": "Succeeded"},
        },
    ]
}

STUB_KUBECTL = """
if [ "$1" = "get" ] && [ "$2" = "--raw=/readyz" ]; then exit 0; fi
if [ "$1" = "get" ] && [ "$2" = "pods" ]; then cat "$PODS_JSON"; exit 0; fi
if [ "$1" = "delete" ] && [ "$2" = "pod" ]; then echo "$*" >> "$RECORD"; exit 0; fi
exit 0
"""


def _run(shell):
    pods_json = shell.tmp / "pods.json"
    pods_json.write_text(json.dumps(PODS))
    record = shell.tmp / "deletes.txt"
    record.write_text("")
    shell.stub("kubectl", STUB_KUBECTL)
    res = shell.run(
        SCRIPT,
        env={"PODS_JSON": str(pods_json), "RECORD": str(record)},
        require=("bash", "jq"),
    )
    return res, record.read_text()


def test_deletes_only_stale_controller_pods(shell):
    res, deletes = _run(shell)
    assert res.returncode == 0  # best-effort: must always exit 0
    # Deleted (controller-owned, stale):
    assert "attproxy-nodelost" in deletes
    assert "attproxy-unknown" in deletes
    assert "agent-failed" in deletes
    # Kept:
    for kept in ("agent-running", "job-succeeded", "bare-unknown", "cuda-validator"):
        assert kept not in deletes, f"{kept} should not be deleted"


def test_unknown_and_nodelost_are_force_deleted(shell):
    _, deletes = _run(shell)
    for line in deletes.splitlines():
        if "attproxy-nodelost" in line or "attproxy-unknown" in line:
            assert "--force" in line and "--grace-period=0" in line
        if "agent-failed" in line:
            # Failed tombstone is deleted gracefully (no force).
            assert "--force" not in line


def test_api_unreachable_exits_zero_without_deleting(shell):
    pods_json = shell.tmp / "pods.json"
    pods_json.write_text(json.dumps(PODS))
    record = shell.tmp / "deletes.txt"
    record.write_text("")
    # readyz fails -> script must skip cleanly and never power off the boot.
    shell.stub(
        "kubectl",
        'if [ "$1" = "get" ] && [ "$2" = "--raw=/readyz" ]; then exit 1; fi\n'
        'if [ "$1" = "delete" ]; then echo "$*" >> "$RECORD"; fi\nexit 0\n',
    )
    res = shell.run(
        SCRIPT,
        env={"PODS_JSON": str(pods_json), "RECORD": str(record)},
        require=("bash",),
    )
    assert res.returncode == 0
    assert record.read_text() == ""
