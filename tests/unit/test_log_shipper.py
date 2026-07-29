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
from sek8s.log_shipper.config import LogShipperConfig
from sek8s.log_shipper.crictl import (
    CrictlError,
    list_chute_pods,
    parse_chute_pods,
    run_crictl,
)
from sek8s.log_shipper.cursor import CursorStore
from sek8s.log_shipper.models import ChutePod, LogLine
from sek8s.log_shipper.shipper import (
    PodLogShipper,
    _chunk,
    _truncate_bytes,
    parse_cri_line,
    read_new_lines,
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


def write_log(pod_dir, container, filename, lines):
    d = pod_dir / container
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text("".join(line + "\n" for line in lines))


# ── config.py ───────────────────────────────────────────────────────────────


def test_logs_url_and_selector_defaults(monkeypatch):
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


def test_terminal_reason_fallback():
    assert PodLogShipper._terminal_reason(410) == "terminal reject"
    assert PodLogShipper._terminal_reason(404) == "unknown config_id"
    assert PodLogShipper._terminal_reason(403) == "cert/ownership rejected"


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


# ── cursor.py ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cursor_missing_file_loads_empty(tmp_path):
    store = CursorStore(tmp_path / "nope" / "cursor.json")
    await store.load()
    assert store.get("x") is None


@pytest.mark.asyncio
async def test_cursor_corrupt_file_loads_empty(tmp_path):
    path = tmp_path / "cursor.json"
    path.write_text("not json{{")
    store = CursorStore(path)
    await store.load()
    assert store.snapshot() == {}


@pytest.mark.asyncio
async def test_cursor_non_dict_file_loads_empty(tmp_path):
    path = tmp_path / "cursor.json"
    path.write_text("[1, 2, 3]")
    store = CursorStore(path)
    await store.load()
    assert store.snapshot() == {}


@pytest.mark.asyncio
async def test_cursor_set_is_monotonic_and_persists(tmp_path):
    path = tmp_path / "sub" / "cursor.json"
    store = CursorStore(path)
    await store.load()
    await store.set("c1", "2026-07-27T00:00:02Z")
    await store.set("c1", "2026-07-27T00:00:01Z")  # older, ignored
    assert store.get("c1") == "2026-07-27T00:00:02Z"
    # Reload from disk to confirm the atomic flush landed.
    reloaded = CursorStore(path)
    await reloaded.load()
    assert reloaded.get("c1") == "2026-07-27T00:00:02Z"


@pytest.mark.asyncio
async def test_cursor_evict_and_reconcile(tmp_path):
    store = CursorStore(tmp_path / "cursor.json")
    await store.load()
    await store.set("a", "t1")
    await store.set("b", "t2")
    await store.set("c", "t3")
    await store.evict("a")
    assert store.get("a") is None
    removed = await store.reconcile({"b"})
    assert removed == 1
    assert store.get("c") is None
    assert store.get("b") == "t2"
    # Evicting a missing key is a no-op.
    await store.evict("missing")


# ── crictl.py ───────────────────────────────────────────────────────────────


def _pods_json(items):
    return json.dumps({"items": items})


def test_parse_chute_pods_filters(tmp_path):
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
    assert pods[0].name == "good"
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


def test_parse_chute_pods_empty_items():
    assert parse_chute_pods(json.dumps({}), make_config()) == []


def test_parse_chute_pods_bad_json():
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
    proc = FakeProc(stdout=b'{"items": []}')

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    out = await run_crictl(make_config(), ["pods", "-o", "json"])
    assert out == '{"items": []}'


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


# ── shipper.py: parsing & reading ────────────────────────────────────────────


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


def test_chunk_by_lines_and_bytes():
    lines = [LogLine(ts=f"t{i}", stream="stdout", log="ab") for i in range(5)]
    by_lines = list(_chunk(lines, max_lines=2, max_bytes=10_000))
    assert [len(b) for b in by_lines] == [2, 2, 1]
    # Each line is 2 bytes; a 3-byte cap forces one line per batch.
    by_bytes = list(_chunk(lines, max_lines=100, max_bytes=3))
    assert [len(b) for b in by_bytes] == [1, 1, 1, 1, 1]


def test_read_new_lines_since_filter(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    pod = ChutePod(config_id="c", name="pod", uid="uid", namespace="chutes")
    write_log(
        tmp_path / pod.log_dir_name,
        "app",
        "0.log",
        [
            "2026-07-27T00:00:01Z stdout F one",
            "2026-07-27T00:00:02Z stdout F two",
            "2026-07-27T00:00:03Z stderr F three",
        ],
    )
    lines = read_new_lines(pod, config, "2026-07-27T00:00:01Z")
    assert [line.log for line in lines] == ["two", "three"]
    assert lines[1].stream == "stderr"


def test_read_new_lines_partial_reassembly(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    pod = ChutePod(config_id="c", name="pod", uid="uid", namespace="chutes")
    write_log(
        tmp_path / pod.log_dir_name,
        "app",
        "0.log",
        [
            "2026-07-27T00:00:01Z stdout P hel",
            "2026-07-27T00:00:01Z stdout P lo ",
            "2026-07-27T00:00:02Z stdout F world",
        ],
    )
    lines = read_new_lines(pod, config, "")
    assert len(lines) == 1
    assert lines[0].log == "hello world"
    assert lines[0].ts == "2026-07-27T00:00:02Z"


def test_read_new_lines_sorts_across_files(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    pod = ChutePod(config_id="c", name="pod", uid="uid", namespace="chutes")
    pod_dir = tmp_path / pod.log_dir_name
    write_log(pod_dir, "app", "0.log", ["2026-07-27T00:00:03Z stdout F c"])
    write_log(pod_dir, "app", "0.log.20260101", ["2026-07-27T00:00:01Z stdout F a"])
    lines = read_new_lines(pod, config, "")
    assert [line.log for line in lines] == ["a", "c"]


def test_read_new_lines_skips_malformed(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    pod = ChutePod(config_id="c", name="pod", uid="uid", namespace="chutes")
    write_log(
        tmp_path / pod.log_dir_name,
        "app",
        "0.log",
        [
            "garbage without fields",
            "2026-07-27T00:00:01Z stdout F good",
        ],
    )
    lines = read_new_lines(pod, config, "")
    assert [line.log for line in lines] == ["good"]


def test_read_new_lines_missing_dir(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    pod = ChutePod(config_id="c", name="gone", uid="uid", namespace="chutes")
    assert read_new_lines(pod, config, "") == []


def test_read_new_lines_respects_max_lines(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path), MAX_LINES_PER_POLL=2)
    pod = ChutePod(config_id="c", name="pod", uid="uid", namespace="chutes")
    write_log(
        tmp_path / pod.log_dir_name,
        "app",
        "0.log",
        [f"2026-07-27T00:00:0{i}Z stdout F line{i}" for i in range(1, 5)],
    )
    lines = read_new_lines(pod, config, "")
    assert len(lines) == 2


def test_read_new_lines_ignores_gz(tmp_path):
    config = make_config(POD_LOG_ROOT=str(tmp_path))
    pod = ChutePod(config_id="c", name="pod", uid="uid", namespace="chutes")
    d = tmp_path / pod.log_dir_name / "app"
    d.mkdir(parents=True)
    (d / "0.log.gz").write_bytes(b"binary garbage not parsed")
    (d / "0.log").write_text("2026-07-27T00:00:01Z stdout F ok\n")
    lines = read_new_lines(pod, config, "")
    assert [line.log for line in lines] == ["ok"]


# ── shipper.py: PodLogShipper ────────────────────────────────────────────────


def make_shipper(config, session, cursor, pod=None):
    pod = pod or ChutePod(
        config_id="cfg",
        name="pod",
        uid="uid",
        namespace="chutes",
        deployment_id="dep-1",
    )
    return PodLogShipper(config, session, pod, cursor)


@pytest.mark.asyncio
async def test_post_batch_ok_and_stop(tmp_path):
    config = make_config()
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    batch = [LogLine(ts="t1", stream="stdout", log="x")]

    ship_ok = make_shipper(config, FakeSession([200]), cursor)
    assert await ship_ok._post_batch(batch) == "ok"

    ship_stop = make_shipper(config, FakeSession([204]), cursor)
    assert await ship_stop._post_batch(batch) == "stop"


@pytest.mark.asyncio
async def test_post_batch_body_carries_deployment_id(tmp_path):
    config = make_config()
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    session = FakeSession([200])
    ship = make_shipper(config, session, cursor)
    await ship._post_batch([LogLine(ts="t1", stream="stdout", log="x")])
    body = session.calls[0]["json"]
    assert body["deployment_id"] == "dep-1"
    assert body["logs"] == [{"ts": "t1", "stream": "stdout", "log": "x"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [(404, "unknown"), (403, "cert/ownership")])
async def test_post_batch_terminal_reject(tmp_path, status, reason):
    config = make_config()
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    session = FakeSession([status])
    ship = make_shipper(config, session, cursor)
    # Terminal codes stop immediately — no retry loop.
    assert (
        await ship._post_batch([LogLine(ts="t", stream="stdout", log="x")])
        == "terminal"
    )
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_post_batch_retries_then_ok(tmp_path):
    config = make_config()
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    session = FakeSession([aiohttp.ClientConnectionError("down"), 200])
    ship = make_shipper(config, session, cursor)
    assert await ship._post_batch([LogLine(ts="t", stream="stdout", log="x")]) == "ok"
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_post_batch_non_2xx_exhausts_to_fail(tmp_path):
    config = make_config()
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    session = FakeSession([500, 503])
    ship = make_shipper(config, session, cursor)
    assert await ship._post_batch([LogLine(ts="t", stream="stdout", log="x")]) == "fail"
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_ship_advances_cursor_and_stops(tmp_path):
    config = make_config(BATCH_MAX_LINES=1)
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    # Two batches: first 200 (advance), second 204 (advance + stop).
    session = FakeSession([200, 204])
    ship = make_shipper(config, session, cursor)
    lines = [
        LogLine(ts="2026-07-27T00:00:01Z", stream="stdout", log="a"),
        LogLine(ts="2026-07-27T00:00:02Z", stream="stdout", log="b"),
    ]
    assert await ship._ship(lines) == "stop"
    assert cursor.get("cfg") == "2026-07-27T00:00:02Z"


@pytest.mark.asyncio
async def test_ship_all_ok_advances_without_stop(tmp_path):
    config = make_config(BATCH_MAX_LINES=1)
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    session = FakeSession([200, 202])
    ship = make_shipper(config, session, cursor)
    lines = [
        LogLine(ts="2026-07-27T00:00:01Z", stream="stdout", log="a"),
        LogLine(ts="2026-07-27T00:00:02Z", stream="stdout", log="b"),
    ]
    assert await ship._ship(lines) == "ok"
    assert cursor.get("cfg") == "2026-07-27T00:00:02Z"


@pytest.mark.asyncio
async def test_ship_transient_failure_leaves_cursor(tmp_path):
    config = make_config()
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    session = FakeSession([500, 500])
    ship = make_shipper(config, session, cursor)
    assert await ship._ship([LogLine(ts="t1", stream="stdout", log="a")]) == "retry"
    assert cursor.get("cfg") is None


@pytest.mark.asyncio
async def test_ship_terminal_reject_leaves_cursor(tmp_path):
    config = make_config()
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    session = FakeSession([404])
    ship = make_shipper(config, session, cursor)
    assert await ship._ship([LogLine(ts="t1", stream="stdout", log="a")]) == "terminal"
    assert cursor.get("cfg") is None


@pytest.mark.asyncio
async def test_run_stops_on_terminal_reject(tmp_path, monkeypatch):
    config = make_config()
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    monkeypatch.setattr(
        "sek8s.log_shipper.shipper.read_new_lines",
        lambda pod, cfg, since: [LogLine(ts="t1", stream="stdout", log="a")],
    )
    ship = make_shipper(config, FakeSession([403]), cursor)
    assert await ship.run() == "terminal"


@pytest.mark.asyncio
async def test_run_stops_on_cutoff(tmp_path, monkeypatch):
    config = make_config()
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    monkeypatch.setattr(
        "sek8s.log_shipper.shipper.read_new_lines",
        lambda pod, cfg, since: [LogLine(ts="t1", stream="stdout", log="a")],
    )
    ship = make_shipper(config, FakeSession([204]), cursor)
    assert await ship.run() == "stop"


@pytest.mark.asyncio
async def test_run_hits_max_duration(tmp_path, monkeypatch):
    config = make_config(MAX_CAPTURE_SECONDS=1.0)
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    monkeypatch.setattr(
        "sek8s.log_shipper.shipper.read_new_lines", lambda pod, cfg, since: []
    )
    clock = iter([0.0, 1000.0, 2000.0])
    pod = ChutePod(config_id="cfg", name="pod", uid="uid", namespace="chutes")
    ship = PodLogShipper(
        config, FakeSession([]), pod, cursor, now_fn=lambda: next(clock)
    )
    assert await ship.run() == "max_duration"


@pytest.mark.asyncio
async def test_run_propagates_cancel(tmp_path, monkeypatch):
    config = make_config()
    cursor = CursorStore(tmp_path / "c.json")
    await cursor.load()
    monkeypatch.setattr(
        "sek8s.log_shipper.shipper.read_new_lines", lambda pod, cfg, since: []
    )
    ship = make_shipper(config, FakeSession([]), cursor)
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

    def __init__(self, config, session, pod, cursor):
        self._pod = pod

    async def run(self):
        FakeShipper.started.append(self._pod.config_id)
        mode = FakeShipper.behavior.get(self._pod.config_id, "hang")
        if mode == "stop":
            return "stop"
        if mode == "error":
            raise RuntimeError("boom")
        await asyncio.Event().wait()  # hang until cancelled


def _pod(config_id):
    return ChutePod(
        config_id=config_id, name=f"n-{config_id}", uid="u", namespace="chutes"
    )


@pytest.mark.asyncio
async def test_poll_discovery_error_is_swallowed(monkeypatch):
    agent = LogShipperAgent(make_config())

    async def boom(_config):
        raise CrictlError("no crictl")

    monkeypatch.setattr("sek8s.log_shipper.agent.list_chute_pods", boom)
    await agent._poll_once()  # must not raise
    assert agent._tasks == {}


@pytest.mark.asyncio
async def test_poll_spawns_and_drops(monkeypatch, tmp_path):
    FakeShipper.behavior = {}
    FakeShipper.started = []
    monkeypatch.setattr("sek8s.log_shipper.agent.PodLogShipper", FakeShipper)
    agent = LogShipperAgent(make_config(CURSOR_PATH=str(tmp_path / "c.json")))
    agent._session = FakeSession([])
    await agent._cursor.load()
    await agent._cursor.set("cfg-1", "t1")

    pods = [_pod("cfg-1")]
    monkeypatch.setattr(
        "sek8s.log_shipper.agent.list_chute_pods",
        lambda _c: _async_return(pods),
    )
    await agent._poll_once()
    await asyncio.sleep(0)
    assert "cfg-1" in agent._tasks
    assert FakeShipper.started == ["cfg-1"]

    # Pod disappears -> task cancelled, cursor evicted.
    monkeypatch.setattr(
        "sek8s.log_shipper.agent.list_chute_pods", lambda _c: _async_return([])
    )
    await agent._poll_once()
    await asyncio.sleep(0)
    assert agent._tasks == {}
    assert agent._cursor.get("cfg-1") is None


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
    # Second poll while at capacity keeps it at 1 (the deferred pod stays deferred).
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
    await asyncio.sleep(0.01)  # let the "stop" task finish
    await agent._poll_once()  # reap + no respawn
    assert agent._tasks == {}
    assert "a" in agent._done
    assert FakeShipper.started == ["a"]  # started exactly once


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
    assert "x" not in agent._done  # cancelled tasks are not marked done


@pytest.mark.asyncio
async def test_agent_run_sets_up_session_and_shuts_down(monkeypatch, tmp_path):
    config = make_config(CURSOR_PATH=str(tmp_path / "c.json"))
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
    assert calls["n"] == 2  # polled, slept, polled again then cancelled
    assert agent._session is not None


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
        coro.close()  # avoid "coroutine never awaited" warning
        raise KeyboardInterrupt()

    monkeypatch.setattr(entry.asyncio, "run", fake_run)
    entry.run()  # must not raise


def _async_return(value):
    async def _coro():
        return value

    return _coro()
