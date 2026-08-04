"""Unit tests for the chute log shipper agent (sek8s.log_shipper.*)."""

from __future__ import annotations

import asyncio
import datetime
import json

import aiohttp
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from sek8s.log_shipper.agent import LogShipperAgent, build_ssl_context
from sek8s.log_shipper.checkpoint import CheckpointStore
from sek8s.log_shipper.config import LogShipperConfig
from sek8s.log_shipper.crictl import (
    CrictlError,
    list_chute_pods,
    parse_chute_pods,
    run_crictl,
)
from sek8s.log_shipper.exceptions import (
    LogStreamingRejected,
    LogStreamingTerminated,
    TransientShipError,
)
from sek8s.log_shipper.models import ChutePod, LogLine
from sek8s.log_shipper.shipper import (
    PodLogShipper,
    _batch_entries,
    _Entry,
    _log_files,
    _log_sort_key,
    _truncate_bytes,
    parse_cri_line,
    parse_logical_lines,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def make_config(**overrides) -> LogShipperConfig:
    """Build a config with fast timings; overrides use env aliases (no populate_by_name)."""
    base = {
        "POLL_INTERVAL_SECONDS": 0.01,
        "RETRY_MAX_ATTEMPTS": 2,
        "RETRY_BASE_DELAY_SECONDS": 0.001,
        "RETRY_MAX_DELAY_SECONDS": 0.001,
    }
    base.update(overrides)
    return LogShipperConfig(**base)


def make_pod(**overrides) -> ChutePod:
    fields = dict(
        config_id="cfg",
        name="pod",
        uid="uid",
        namespace="chutes",
        deployment_id="dep-1",
    )
    fields.update(overrides)
    return ChutePod(**fields)


async def make_checkpoints(tmp_path) -> CheckpointStore:
    store = CheckpointStore(tmp_path / "checkpoint.json")
    await store.load()
    return store


def cri(ts, msg, stream="stdout", tag="F") -> str:
    return f"{ts} {stream} {tag} {msg}"


def write_log_file(root, pod, container, name, lines) -> "object":
    directory = root / pod.log_dir_name / container
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("".join(line + "\n" for line in lines))
    return path


class FakeResp:
    def __init__(self, status: int):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakePost:
    def __init__(self, outcome):
        self._outcome = outcome  # int status or Exception

    async def __aenter__(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return FakeResp(self._outcome)

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Minimal stand-in for aiohttp.ClientSession.post."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        outcome = self.outcomes.pop(0) if self.outcomes else 200
        return FakePost(outcome)


# ── config.py ───────────────────────────────────────────────────────────────


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("VALIDATOR_BASE_URL", raising=False)
    config = LogShipperConfig()
    assert (
        config.logs_url("abc")
        == "https://cvm.chutes.ai/instances/launch_config/abc/logs"
    )
    assert config.selector_key == "chutes/chute"
    assert config.selector_value == "true"
    assert config.stop_status_code == 204
    assert config.terminal_status_codes == [403, 404]
    assert config.deployment_id_label == "chutes/deployment-id"
    assert config.buffer_bytes == 1_048_576
    assert config.checkpoint_path.name == "checkpoint.json"
    assert config.container_name == "chute"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("[401, 410]", [401, 410]),
        ("401,410", [401, 410]),
        ("403", [403]),
        ([401, 402], [401, 402]),
    ],
)
def test_terminal_status_codes_parsing(raw, expected):
    config = LogShipperConfig(TERMINAL_STATUS_CODES=raw)
    assert config.terminal_status_codes == expected


def test_rejection_reason():
    assert PodLogShipper._rejection_reason(410) == "rejected"
    assert PodLogShipper._rejection_reason(404) == "unknown config_id"
    assert PodLogShipper._rejection_reason(403) == "cert/ownership rejected"


def test_logs_url_strips_trailing_slash():
    config = LogShipperConfig(VALIDATOR_BASE_URL="https://cvm.chutes.ai/")
    assert (
        config.logs_url("x") == "https://cvm.chutes.ai/instances/launch_config/x/logs"
    )


@pytest.mark.parametrize("bad", ["noequals", "=value", "key="])
def test_invalid_label_selector_rejected(bad):
    with pytest.raises(ValueError):
        LogShipperConfig(LABEL_SELECTOR=bad)


# ── models.py ───────────────────────────────────────────────────────────────


def test_chute_pod_log_dir_name():
    pod = ChutePod(config_id="c", name="pod-a", uid="uid-1", namespace="chutes")
    assert pod.log_dir_name == "chutes_pod-a_uid-1"


# ── checkpoint.py ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_checkpoint_missing_file_loads_empty(tmp_path):
    store = CheckpointStore(tmp_path / "nope" / "checkpoint.json")
    await store.load()
    assert store.get("x") == {}


@pytest.mark.asyncio
async def test_checkpoint_corrupt_and_non_dict_load_empty(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text("not json{{")
    store = CheckpointStore(path)
    await store.load()
    assert store.snapshot() == {}
    path.write_text("[1, 2, 3]")
    store2 = CheckpointStore(path)
    await store2.load()
    assert store2.snapshot() == {}


@pytest.mark.asyncio
async def test_checkpoint_set_get_persists(tmp_path):
    path = tmp_path / "sub" / "checkpoint.json"
    store = CheckpointStore(path)
    await store.load()
    await store.set("c1", {"7": 100, "8": 200})
    assert store.get("c1") == {"7": 100, "8": 200}
    # get returns a copy — mutating it must not affect the store.
    store.get("c1")["7"] = 0
    assert store.get("c1")["7"] == 100
    reloaded = CheckpointStore(path)
    await reloaded.load()
    assert reloaded.get("c1") == {"7": 100, "8": 200}


@pytest.mark.asyncio
async def test_checkpoint_evict_and_reconcile(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoint.json")
    await store.load()
    await store.set("a", {"1": 1})
    await store.set("b", {"1": 2})
    await store.set("c", {"1": 3})
    await store.evict("a")
    assert store.get("a") == {}
    removed = await store.reconcile({"b"})
    assert removed == 1
    assert store.get("c") == {}
    assert store.get("b") == {"1": 2}
    await store.evict("missing")  # no-op


# ── crictl.py ───────────────────────────────────────────────────────────────


def _pods_json(items):
    return json.dumps({"items": items})


def test_parse_chute_pods_filters():
    config = make_config()
    raw = _pods_json(
        [
            {
                "metadata": {"name": "good", "uid": "u1", "namespace": "chutes"},
                "state": "SANDBOX_READY",
                "labels": {
                    "chutes/chute": "true",
                    "chutes/config-id": "cfg-1",
                    "chutes/deployment-id": "dep-1",
                },
            },
            {  # wrong namespace
                "metadata": {"name": "n", "uid": "u2", "namespace": "default"},
                "labels": {"chutes/chute": "true", "chutes/config-id": "cfg-2"},
            },
            {  # selector mismatch
                "metadata": {"name": "n", "uid": "u3", "namespace": "chutes"},
                "labels": {"chutes/chute": "false", "chutes/config-id": "cfg-3"},
            },
            {  # missing config-id label
                "metadata": {"name": "n", "uid": "u4", "namespace": "chutes"},
                "labels": {"chutes/chute": "true"},
            },
            {  # missing uid
                "metadata": {"name": "n", "namespace": "chutes"},
                "labels": {"chutes/chute": "true", "chutes/config-id": "cfg-5"},
            },
        ]
    )
    pods = parse_chute_pods(raw, config)
    assert len(pods) == 1
    assert pods[0].config_id == "cfg-1"
    assert pods[0].deployment_id == "dep-1"
    assert pods[0].state == "SANDBOX_READY"


def test_parse_chute_pods_missing_deployment_id_defaults_empty():
    raw = _pods_json(
        [
            {
                "metadata": {"name": "n", "uid": "u", "namespace": "chutes"},
                "labels": {"chutes/chute": "true", "chutes/config-id": "cfg"},
            }
        ]
    )
    pods = parse_chute_pods(raw, make_config())
    assert pods[0].deployment_id == ""


def test_parse_chute_pods_empty_and_bad():
    assert parse_chute_pods(json.dumps({}), make_config()) == []
    with pytest.raises(CrictlError):
        parse_chute_pods("not json", make_config())


class FakeProc:
    def __init__(self, stdout=b"", stderr=b"", returncode=0, hang=False):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(10)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


@pytest.mark.asyncio
async def test_run_crictl_success(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return FakeProc(stdout=b'{"items": []}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await run_crictl(make_config(), ["pods", "-o", "json"]) == '{"items": []}'


@pytest.mark.asyncio
async def test_run_crictl_nonzero_exit(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return FakeProc(stderr=b"boom", returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(CrictlError, match="exited 1"):
        await run_crictl(make_config(), ["pods"])


@pytest.mark.asyncio
async def test_run_crictl_missing_wrapper(monkeypatch):
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(CrictlError, match="not found"):
        await run_crictl(make_config(), ["pods"])


@pytest.mark.asyncio
async def test_run_crictl_timeout(monkeypatch):
    proc = FakeProc(hang=True)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(CrictlError, match="timed out"):
        await run_crictl(make_config(COMMAND_TIMEOUT_SECONDS=0.01), ["pods"])
    assert proc.killed


@pytest.mark.asyncio
async def test_list_chute_pods(monkeypatch):
    raw = _pods_json(
        [
            {
                "metadata": {"name": "p", "uid": "u", "namespace": "chutes"},
                "labels": {"chutes/chute": "true", "chutes/config-id": "cfg"},
            }
        ]
    )

    async def fake_exec(*args, **kwargs):
        return FakeProc(stdout=raw.encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    pods = await list_chute_pods(make_config())
    assert [p.config_id for p in pods] == ["cfg"]


# ── shipper.py: parsing ───────────────────────────────────────────────────────


def test_parse_cri_line_variants():
    assert parse_cri_line("2026-07-27T00:00:00Z stdout F hi") == (
        "2026-07-27T00:00:00Z",
        "stdout",
        "F",
        "hi",
    )
    assert parse_cri_line("2026-07-27T00:00:00Z stderr F") == (
        "2026-07-27T00:00:00Z",
        "stderr",
        "F",
        "",
    )
    assert parse_cri_line("") is None
    assert parse_cri_line("too short") is None
    assert parse_cri_line("ts weirdstream F msg") is None
    assert parse_cri_line("ts stdout X msg") is None


def test_truncate_bytes():
    assert _truncate_bytes("hello", 100) == "hello"
    assert _truncate_bytes("hello", 3) == "hel"


def test_parse_logical_lines_basic_and_offsets():
    line1 = cri("2026-07-27T00:00:01Z", "hello") + "\n"
    line2 = cri("2026-07-27T00:00:02Z", "world", stream="stderr") + "\n"
    data = (line1 + line2).encode()
    result = parse_logical_lines(data, 16_384)
    assert [(ll.log, ll.stream) for ll, _ in result] == [
        ("hello", "stdout"),
        ("world", "stderr"),
    ]
    # end offsets point just past each line's terminating newline.
    assert result[0][1] == len(line1.encode())
    assert result[1][1] == len(data)


def test_parse_logical_lines_partial_reassembly():
    data = (
        cri("2026-07-27T00:00:01Z", "hel", tag="P")
        + "\n"
        + cri("2026-07-27T00:00:01Z", "lo ", tag="P")
        + "\n"
        + cri("2026-07-27T00:00:02Z", "world", tag="F")
        + "\n"
    ).encode()
    result = parse_logical_lines(data, 16_384)
    assert len(result) == 1
    assert result[0][0].log == "hello world"
    assert result[0][0].ts == "2026-07-27T00:00:02Z"
    assert result[0][1] == len(data)  # committed through the F line


def test_parse_logical_lines_excludes_incomplete_physical_line():
    complete = cri("2026-07-27T00:00:01Z", "a") + "\n"
    data = (complete + "2026-07-27T00:00:02Z stdout F partial-no-newline").encode()
    result = parse_logical_lines(data, 16_384)
    assert [ll.log for ll, _ in result] == ["a"]
    assert result[0][1] == len(complete.encode())  # offset stops before the fragment


def test_parse_logical_lines_excludes_trailing_p_run():
    complete = cri("2026-07-27T00:00:01Z", "a") + "\n"
    trailing_p = cri("2026-07-27T00:00:02Z", "beg", tag="P") + "\n"
    data = (complete + trailing_p).encode()
    result = parse_logical_lines(data, 16_384)
    assert [ll.log for ll, _ in result] == ["a"]
    assert result[0][1] == len(complete.encode())


def test_parse_logical_lines_skips_malformed_and_truncates():
    data = (
        "garbage without fields\n" + cri("2026-07-27T00:00:01Z", "abcdefgh") + "\n"
    ).encode()
    result = parse_logical_lines(data, 3)
    assert [ll.log for ll, _ in result] == ["abc"]  # truncated to max_line_bytes


def test_batch_entries():
    entries = [
        _Entry(LogLine(ts=f"t{i}", stream="stdout", log="ab"), inode=1, end_offset=i)
        for i in range(5)
    ]
    by_lines = list(_batch_entries(entries, max_lines=2, max_bytes=10_000))
    assert [len(b) for b in by_lines] == [2, 2, 1]
    by_bytes = list(_batch_entries(entries, max_lines=100, max_bytes=3))
    assert [len(b) for b in by_bytes] == [1, 1, 1, 1, 1]


def test_log_sort_key_and_files_ordering(tmp_path):
    pod = make_pod()
    directory = tmp_path / pod.log_dir_name / "chute"
    directory.mkdir(parents=True)
    names = ["0.log", "0.log.20260101-01", "0.log.20260101-02", "1.log"]
    for name in names:
        (directory / name).write_text("")
    ordered = [p.name for p in _log_files(tmp_path / pod.log_dir_name, "chute")]
    # rotated (oldest->newest) then current, per restart index ascending.
    assert ordered == ["0.log.20260101-01", "0.log.20260101-02", "0.log", "1.log"]
    # unknown names sort deterministically (fall to the front group).
    assert _log_sort_key(directory / "weird.txt")[0] == 0


# ── shipper.py: _read_window ──────────────────────────────────────────────────


def make_shipper(config, session, checkpoints, pod=None) -> PodLogShipper:
    return PodLogShipper(config, session, pod or make_pod(), checkpoints)


@pytest.mark.asyncio
async def test_read_window_fresh_read_does_not_advance_offsets(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    checkpoints = await make_checkpoints(tmp_path)
    pod = make_pod()
    ship = make_shipper(config, FakeSession([]), checkpoints, pod)
    write_log_file(
        tmp_path,
        pod,
        "chute",
        "0.log",
        [cri("2026-07-27T00:00:01Z", "a"), cri("2026-07-27T00:00:02Z", "b")],
    )
    entries, hit_budget = ship._read_window()
    assert [e.line.log for e in entries] == ["a", "b"]
    assert not hit_budget
    assert ship._offsets == {}  # committed only on ship


@pytest.mark.asyncio
async def test_read_window_incremental_by_offset(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    checkpoints = await make_checkpoints(tmp_path)
    pod = make_pod()
    ship = make_shipper(config, FakeSession([]), checkpoints, pod)
    path = write_log_file(
        tmp_path,
        pod,
        "chute",
        "0.log",
        [cri("2026-07-27T00:00:01Z", "a"), cri("2026-07-27T00:00:02Z", "b")],
    )
    inode = path.stat().st_ino
    entries, _ = ship._read_window()
    # Simulate the first line being shipped/committed, then read again.
    ship._offsets = {inode: entries[0].end_offset}
    entries2, _ = ship._read_window()
    assert [e.line.log for e in entries2] == ["b"]


@pytest.mark.asyncio
async def test_read_window_truncation_resets(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    checkpoints = await make_checkpoints(tmp_path)
    pod = make_pod()
    ship = make_shipper(config, FakeSession([]), checkpoints, pod)
    path = write_log_file(
        tmp_path, pod, "chute", "0.log", [cri("2026-07-27T00:00:01Z", "old")]
    )
    inode = path.stat().st_ino
    ship._offsets = {inode: 9999}  # committed past a now-smaller file
    path.write_text(cri("2026-07-27T00:00:02Z", "new") + "\n")  # same inode, smaller
    entries, _ = ship._read_window()
    assert [e.line.log for e in entries] == ["new"]


@pytest.mark.asyncio
async def test_read_window_follows_rotation_by_inode(tmp_path):
    import os

    config = make_config(POD_LOG_ROOT=str(tmp_path))
    checkpoints = await make_checkpoints(tmp_path)
    pod = make_pod()
    ship = make_shipper(config, FakeSession([]), checkpoints, pod)
    directory = tmp_path / pod.log_dir_name / "chute"
    current = write_log_file(
        tmp_path,
        pod,
        "chute",
        "0.log",
        [cri("2026-07-27T00:00:01Z", "a1"), cri("2026-07-27T00:00:02Z", "a2")],
    )
    inode1 = current.stat().st_ino
    ship._offsets = {inode1: current.stat().st_size}  # fully committed
    # Rotate: rename keeps the inode; a fresh 0.log gets a new inode.
    os.rename(current, directory / "0.log.20260101-01")
    write_log_file(tmp_path, pod, "chute", "0.log", [cri("2026-07-27T00:00:03Z", "b1")])
    entries, _ = ship._read_window()
    # The renamed file (same inode, fully committed) yields nothing; only new file reads.
    assert [e.line.log for e in entries] == ["b1"]


@pytest.mark.asyncio
async def test_read_window_budget_caps_read(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path), BUFFER_BYTES=65_536)
    checkpoints = await make_checkpoints(tmp_path)
    pod = make_pod()
    ship = make_shipper(config, FakeSession([]), checkpoints, pod)
    # ~1000 lines * ~40 bytes ≈ 40 KB < 64 KB... make it exceed the window.
    lines = [cri(f"2026-07-27T00:00:{i:02d}Z", "x" * 60) for i in range(1200)]
    write_log_file(tmp_path, pod, "chute", "0.log", lines)
    entries, hit_budget = ship._read_window()
    assert hit_budget
    assert 0 < len(entries) < 1200


@pytest.mark.asyncio
async def test_read_window_budget_stops_before_next_file(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path), BUFFER_BYTES=65_536)
    checkpoints = await make_checkpoints(tmp_path)
    pod = make_pod()
    ship = make_shipper(config, FakeSession([]), checkpoints, pod)
    # First file (restart 0) fills the whole window; the second is not reached.
    write_log_file(
        tmp_path,
        pod,
        "chute",
        "0.log",
        [cri(f"2026-07-27T00:00:{i:02d}Z", "x" * 60) for i in range(1200)],
    )
    write_log_file(
        tmp_path, pod, "chute", "1.log", [cri("2026-07-27T01:00:00Z", "second-file")]
    )
    entries, hit_budget = ship._read_window()
    assert hit_budget
    assert all(e.line.log != "second-file" for e in entries)


@pytest.mark.asyncio
async def test_read_window_ignores_gz_and_missing_dir(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    checkpoints = await make_checkpoints(tmp_path)
    pod = make_pod()
    ship = make_shipper(config, FakeSession([]), checkpoints, pod)
    # Missing pod dir -> nothing.
    assert ship._read_window() == ([], False)
    directory = tmp_path / pod.log_dir_name / "chute"
    directory.mkdir(parents=True)
    (directory / "0.log.gz").write_bytes(b"binary garbage")
    write_log_file(tmp_path, pod, "chute", "0.log", [cri("2026-07-27T00:00:01Z", "ok")])
    entries, _ = ship._read_window()
    assert [e.line.log for e in entries] == ["ok"]


@pytest.mark.asyncio
async def test_read_window_captures_only_chute_container(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    checkpoints = await make_checkpoints(tmp_path)
    pod = make_pod()
    ship = make_shipper(config, FakeSession([]), checkpoints, pod)
    # Init/sidecar containers are ignored — only the "chute" container is shipped.
    write_log_file(
        tmp_path, pod, "init", "0.log", [cri("2026-07-27T00:00:00Z", "init-log")]
    )
    write_log_file(
        tmp_path, pod, "chute", "0.log", [cri("2026-07-27T00:00:01Z", "chute-log")]
    )
    entries, _ = ship._read_window()
    assert [e.line.log for e in entries] == ["chute-log"]


@pytest.mark.asyncio
async def test_read_window_prunes_gone_inodes(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    checkpoints = await make_checkpoints(tmp_path)
    pod = make_pod()
    ship = make_shipper(config, FakeSession([]), checkpoints, pod)
    write_log_file(tmp_path, pod, "chute", "0.log", [cri("2026-07-27T00:00:01Z", "a")])
    ship._offsets = {987654321: 5}  # an inode that isn't present
    ship._read_window()
    assert 987654321 not in ship._offsets


# ── shipper.py: _ship / _post_batch ───────────────────────────────────────────


def _entry(ts, msg, inode, end_offset):
    return _Entry(LogLine(ts=ts, stream="stdout", log=msg), inode, end_offset)


@pytest.mark.asyncio
async def test_ship_commits_offsets_and_persists(tmp_path):
    config = make_config()
    checkpoints = await make_checkpoints(tmp_path)
    session = FakeSession([200])
    ship = make_shipper(config, session, checkpoints)
    await ship._ship([_entry("t1", "a", inode=7, end_offset=10)])
    assert ship._offsets == {7: 10}
    assert checkpoints.get("cfg") == {"7": 10}
    body = session.calls[0]["json"]
    assert body["deployment_id"] == "dep-1"
    assert body["logs"] == [{"ts": "t1", "stream": "stdout", "log": "a"}]


@pytest.mark.asyncio
async def test_ship_terminated_does_not_commit(tmp_path):
    config = make_config(BATCH_MAX_LINES=1)
    checkpoints = await make_checkpoints(tmp_path)
    # first batch 200 (commit), second 204 (raise before commit).
    ship = make_shipper(config, FakeSession([200, 204]), checkpoints)
    entries = [
        _entry("t1", "a", inode=7, end_offset=10),
        _entry("t2", "b", inode=7, end_offset=20),
    ]
    with pytest.raises(LogStreamingTerminated):
        await ship._ship(entries)
    assert ship._offsets == {7: 10}  # only the accepted batch committed


@pytest.mark.asyncio
async def test_ship_transient_leaves_offsets(tmp_path):
    config = make_config()
    checkpoints = await make_checkpoints(tmp_path)
    ship = make_shipper(config, FakeSession([500, 500]), checkpoints)
    with pytest.raises(TransientShipError):
        await ship._ship([_entry("t1", "a", inode=7, end_offset=10)])
    assert ship._offsets == {}


@pytest.mark.asyncio
async def test_ship_splits_batch_on_413(tmp_path):
    config = make_config()
    checkpoints = await make_checkpoints(tmp_path)
    # 413 on the 2-line batch → split → each half posts 200.
    session = FakeSession([413, 200, 200])
    ship = make_shipper(config, session, checkpoints)
    before = ship._max_batch_bytes
    await ship._ship(
        [
            _entry("t1", "a", inode=7, end_offset=10),
            _entry("t2", "b", inode=7, end_offset=20),
        ]
    )
    assert len(session.calls) == 3  # 1 rejected + 2 halves
    assert ship._offsets == {7: 20}
    assert ship._max_batch_bytes == before // 2  # shrank the ceiling


@pytest.mark.asyncio
async def test_ship_413_single_line_skips_to_progress(tmp_path):
    config = make_config()
    checkpoints = await make_checkpoints(tmp_path)
    session = FakeSession([413])  # a lone line that still 413s can't be split further
    ship = make_shipper(config, session, checkpoints)
    await ship._ship([_entry("t1", "a", inode=7, end_offset=10)])
    assert len(session.calls) == 1
    assert ship._offsets == {7: 10}  # committed past it — no livelock


@pytest.mark.asyncio
async def test_post_batch_ok_returns_none(tmp_path):
    ship = make_shipper(
        make_config(), FakeSession([200]), await make_checkpoints(tmp_path)
    )
    assert await ship._post_batch([LogLine(ts="t", stream="stdout", log="x")]) is None


@pytest.mark.asyncio
async def test_post_batch_cutoff_and_reject(tmp_path):
    checkpoints = await make_checkpoints(tmp_path)
    batch = [LogLine(ts="t", stream="stdout", log="x")]
    ship204 = make_shipper(make_config(), FakeSession([204]), checkpoints)
    with pytest.raises(LogStreamingTerminated):
        await ship204._post_batch(batch)
    ship403 = make_shipper(make_config(), FakeSession([403]), checkpoints)
    with pytest.raises(LogStreamingRejected) as exc:
        await ship403._post_batch(batch)
    assert exc.value.status == 403
    assert exc.value.reason == "cert/ownership rejected"


@pytest.mark.asyncio
async def test_post_batch_retry_then_ok_and_exhaust(tmp_path):
    checkpoints = await make_checkpoints(tmp_path)
    batch = [LogLine(ts="t", stream="stdout", log="x")]
    retry = FakeSession([aiohttp.ClientConnectionError("down"), 200])
    ship = make_shipper(make_config(), retry, checkpoints)
    assert await ship._post_batch(batch) is None
    assert len(retry.calls) == 2
    exhaust = FakeSession([500, 503])
    ship2 = make_shipper(make_config(), exhaust, checkpoints)
    with pytest.raises(TransientShipError):
        await ship2._post_batch(batch)
    assert len(exhaust.calls) == 2


# ── shipper.py: run (end-to-end) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_streams_from_disk_then_terminates(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    checkpoints = await make_checkpoints(tmp_path)
    pod = make_pod()
    session = FakeSession([204])
    ship = make_shipper(config, session, checkpoints, pod)
    write_log_file(
        tmp_path, pod, "chute", "0.log", [cri("2026-07-27T00:00:01Z", "hello")]
    )
    assert await ship.run() is None
    assert session.calls[0]["json"]["logs"] == [
        {"ts": "2026-07-27T00:00:01Z", "stream": "stdout", "log": "hello"}
    ]


@pytest.mark.asyncio
async def test_run_terminates_on_204(tmp_path, monkeypatch):
    ship = make_shipper(
        make_config(), FakeSession([204]), await make_checkpoints(tmp_path)
    )
    monkeypatch.setattr(
        ship, "_read_window", lambda: ([_entry("t1", "a", 1, 10)], False)
    )
    assert await ship.run() is None


@pytest.mark.asyncio
async def test_run_stops_on_rejection(tmp_path, monkeypatch):
    session = FakeSession([403])
    ship = make_shipper(make_config(), session, await make_checkpoints(tmp_path))
    monkeypatch.setattr(
        ship, "_read_window", lambda: ([_entry("t1", "a", 1, 10)], False)
    )
    assert await ship.run() is None
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_run_retries_after_transient(tmp_path, monkeypatch):
    session = FakeSession([500, 500, 204])  # poll1 transient (2 attempts), poll2 -> 204
    ship = make_shipper(make_config(), session, await make_checkpoints(tmp_path))
    monkeypatch.setattr(
        ship, "_read_window", lambda: ([_entry("t1", "a", 1, 10)], False)
    )
    assert await ship.run() is None
    assert len(session.calls) == 3


@pytest.mark.asyncio
async def test_run_drains_backlog_without_sleeping(tmp_path, monkeypatch):
    # hit_budget True keeps the loop draining (no idle sleep) until a stop signal.
    session = FakeSession([200, 200, 204])
    ship = make_shipper(make_config(), session, await make_checkpoints(tmp_path))
    monkeypatch.setattr(
        ship, "_read_window", lambda: ([_entry("t1", "a", 1, 10)], True)
    )
    slept = []
    monkeypatch.setattr(
        "sek8s.log_shipper.shipper.asyncio.sleep",
        lambda s: slept.append(s) or asyncio.sleep(0),
    )
    assert await ship.run() is None
    assert len(session.calls) == 3
    assert slept == []  # never idled while draining a full window


@pytest.mark.asyncio
async def test_run_propagates_cancel(tmp_path, monkeypatch):
    ship = make_shipper(
        make_config(), FakeSession([]), await make_checkpoints(tmp_path)
    )
    monkeypatch.setattr(ship, "_read_window", lambda: ([], False))
    task = asyncio.create_task(ship.run())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── agent.py ─────────────────────────────────────────────────────────────────


def _self_signed(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2030, 1, 1))
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "client.crt"
    key_path = tmp_path / "client.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def test_build_ssl_context(tmp_path):
    cert_path, key_path = _self_signed(tmp_path)
    config = make_config(MTLS_CERT_PATH=str(cert_path), MTLS_KEY_PATH=str(key_path))
    context = build_ssl_context(config)
    assert context.verify_mode.name == "CERT_REQUIRED"


class FakeShipper:
    behavior: dict = {}
    started: list = []

    def __init__(self, config, session, pod, checkpoints):
        self._pod = pod

    async def run(self):
        FakeShipper.started.append(self._pod.config_id)
        mode = FakeShipper.behavior.get(self._pod.config_id, "hang")
        if mode == "stop":
            return None
        if mode == "error":
            raise RuntimeError("boom")
        await asyncio.Event().wait()  # hang until cancelled


def _pod(config_id):
    return ChutePod(
        config_id=config_id, name=f"n-{config_id}", uid="u", namespace="chutes"
    )


def _async_return(value):
    async def _coro():
        return value

    return _coro()


@pytest.mark.asyncio
async def test_poll_discovery_error_is_swallowed(monkeypatch):
    agent = LogShipperAgent(make_config())

    async def boom(_config):
        raise CrictlError("no crictl")

    monkeypatch.setattr("sek8s.log_shipper.agent.list_chute_pods", boom)
    await agent._poll_once()
    assert agent._tasks == {}


@pytest.mark.asyncio
async def test_poll_spawns_and_drops(monkeypatch, tmp_path):
    FakeShipper.behavior = {}
    FakeShipper.started = []
    monkeypatch.setattr("sek8s.log_shipper.agent.PodLogShipper", FakeShipper)
    agent = LogShipperAgent(make_config(CHECKPOINT_PATH=str(tmp_path / "c.json")))
    agent._session = FakeSession([])
    await agent._checkpoints.load()
    await agent._checkpoints.set("cfg-1", {"7": 5})

    pods = [_pod("cfg-1")]
    monkeypatch.setattr(
        "sek8s.log_shipper.agent.list_chute_pods", lambda _c: _async_return(pods)
    )
    await agent._poll_once()
    await asyncio.sleep(0)
    assert "cfg-1" in agent._tasks
    assert FakeShipper.started == ["cfg-1"]

    monkeypatch.setattr(
        "sek8s.log_shipper.agent.list_chute_pods", lambda _c: _async_return([])
    )
    await agent._poll_once()
    await asyncio.sleep(0)
    assert agent._tasks == {}
    assert agent._checkpoints.get("cfg-1") == {}  # evicted


@pytest.mark.asyncio
async def test_poll_respects_capacity(monkeypatch):
    FakeShipper.behavior = {}
    FakeShipper.started = []
    monkeypatch.setattr("sek8s.log_shipper.agent.PodLogShipper", FakeShipper)
    agent = LogShipperAgent(make_config(MAX_CONCURRENT_PODS=1))
    agent._session = FakeSession([])
    pods = [_pod("a"), _pod("b")]
    monkeypatch.setattr(
        "sek8s.log_shipper.agent.list_chute_pods", lambda _c: _async_return(pods)
    )
    await agent._poll_once()
    await asyncio.sleep(0)
    assert len(agent._tasks) == 1
    await agent._poll_once()
    await asyncio.sleep(0)
    assert len(agent._tasks) == 1
    await agent._shutdown()


@pytest.mark.asyncio
async def test_poll_reaps_finished_and_does_not_respawn(monkeypatch):
    FakeShipper.behavior = {"a": "stop"}
    FakeShipper.started = []
    monkeypatch.setattr("sek8s.log_shipper.agent.PodLogShipper", FakeShipper)
    agent = LogShipperAgent(make_config())
    agent._session = FakeSession([])
    monkeypatch.setattr(
        "sek8s.log_shipper.agent.list_chute_pods", lambda _c: _async_return([_pod("a")])
    )
    await agent._poll_once()
    await asyncio.sleep(0.01)
    await agent._poll_once()
    assert agent._tasks == {}
    assert "a" in agent._done
    assert FakeShipper.started == ["a"]


@pytest.mark.asyncio
async def test_poll_reaps_errored_task(monkeypatch):
    FakeShipper.behavior = {"a": "error"}
    FakeShipper.started = []
    monkeypatch.setattr("sek8s.log_shipper.agent.PodLogShipper", FakeShipper)
    agent = LogShipperAgent(make_config())
    agent._session = FakeSession([])
    monkeypatch.setattr(
        "sek8s.log_shipper.agent.list_chute_pods", lambda _c: _async_return([_pod("a")])
    )
    await agent._poll_once()
    await asyncio.sleep(0.01)
    await agent._poll_once()
    assert "a" in agent._done
    assert agent._tasks == {}


@pytest.mark.asyncio
async def test_reap_skips_cancelled_task():
    agent = LogShipperAgent(make_config())

    async def hang():
        await asyncio.Event().wait()

    task = asyncio.create_task(hang())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    agent._tasks["x"] = task
    agent._pods["x"] = _pod("x")
    agent._reap_finished()
    assert "x" not in agent._tasks
    assert "x" not in agent._done


@pytest.mark.asyncio
async def test_agent_run_sets_up_session_and_shuts_down(monkeypatch, tmp_path):
    config = make_config(CHECKPOINT_PATH=str(tmp_path / "c.json"))
    agent = LogShipperAgent(config)
    monkeypatch.setattr("sek8s.log_shipper.agent.build_ssl_context", lambda c: None)

    class FakeConnector:
        def __init__(self, ssl=None):
            pass

    class FakeClientSession:
        def __init__(self, connector=None):
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            self.closed = True
            return False

    monkeypatch.setattr("sek8s.log_shipper.agent.aiohttp.TCPConnector", FakeConnector)
    monkeypatch.setattr(
        "sek8s.log_shipper.agent.aiohttp.ClientSession", FakeClientSession
    )

    calls = {"n": 0}

    async def fake_poll():
        calls["n"] += 1
        if calls["n"] >= 2:
            raise asyncio.CancelledError()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(agent, "_poll_once", fake_poll)
    monkeypatch.setattr("sek8s.log_shipper.agent.asyncio.sleep", no_sleep)
    with pytest.raises(asyncio.CancelledError):
        await agent.run()
    assert calls["n"] == 2
    assert agent._session is not None


@pytest.mark.asyncio
async def test_shutdown_cancels_tasks(monkeypatch):
    FakeShipper.behavior = {}
    FakeShipper.started = []
    monkeypatch.setattr("sek8s.log_shipper.agent.PodLogShipper", FakeShipper)
    agent = LogShipperAgent(make_config())
    agent._session = FakeSession([])
    monkeypatch.setattr(
        "sek8s.log_shipper.agent.list_chute_pods", lambda _c: _async_return([_pod("a")])
    )
    await agent._poll_once()
    await asyncio.sleep(0)
    await agent._shutdown()
    assert agent._tasks == {}


# ── services/log_shipper.py entrypoint ───────────────────────────────────────


def test_service_run_invokes_agent(monkeypatch):
    import sek8s.services.log_shipper as entry

    started = {"ran": False}

    class FakeAgent:
        def __init__(self, config):
            pass

        async def run(self):
            started["ran"] = True

    monkeypatch.setattr(entry, "LogShipperAgent", FakeAgent)
    entry.run()
    assert started["ran"] is True


def test_service_run_handles_keyboard_interrupt(monkeypatch):
    import sek8s.services.log_shipper as entry

    def fake_run(coro):
        coro.close()
        raise KeyboardInterrupt()

    monkeypatch.setattr(entry.asyncio, "run", fake_run)
    entry.run()  # must not raise
