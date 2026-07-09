"""Unit tests for cache delete and re-download permission handling.

A chute pod runs as uid 1000 and can download model files directly into the
shared cache, leaving blob dirs/files the system-manager user neither owns nor
can write into. Two failure modes were observed in production:

* ``cache-delete`` returned HTTP 500 because ``shutil.rmtree`` raised
  ``PermissionError`` (EACCES, errno 13) and the sudo-rm fallback only triggered
  on EPERM (errno 1).
* re-download failed mid-way with ``PermissionError [Errno 13]`` because the
  download subprocess could not write into the pod-owned partial.

These tests pin both fixes: the sudo-rm fallback fires for EACCES, foreign-owned
trees are detected, and re-download clears them first.
"""

from __future__ import annotations

import errno
import os
import shutil
from types import SimpleNamespace

import pytest

from sek8s.config import cache_config
from sek8s.system_manager.cache.manager import HuggingFaceSnapshot

CHUTE_ID = "2ff25e81-4586-5ec8-b892-3a6f342693d7"


@pytest.fixture
def cache_base(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_config, "cache_base", str(tmp_path))
    return tmp_path


def _make_tree(snap: HuggingFaceSnapshot) -> None:
    blobs = snap.hub_path / "models--foo--bar" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "deadbeef.incomplete").write_text("partial")


def test_has_foreign_entries_false_when_all_owned(cache_base):
    snap = HuggingFaceSnapshot(chute_id=CHUTE_ID)
    _make_tree(snap)
    # Everything we just created is owned by us.
    assert snap._has_foreign_entries() is False


def test_has_foreign_entries_true_for_foreign_file(cache_base, monkeypatch):
    snap = HuggingFaceSnapshot(chute_id=CHUTE_ID)
    _make_tree(snap)
    foreign = snap.hub_path / "models--foo--bar" / "blobs" / "deadbeef.incomplete"

    real_lstat = os.lstat

    def fake_lstat(path, *args, **kwargs):
        st = real_lstat(path, *args, **kwargs)
        if str(path) == str(foreign):
            return SimpleNamespace(st_uid=os.getuid() + 1, st_mode=st.st_mode)
        return st

    monkeypatch.setattr(os, "lstat", fake_lstat)
    assert snap._has_foreign_entries() is True


def test_has_foreign_entries_true_when_missing(cache_base):
    snap = HuggingFaceSnapshot(chute_id=CHUTE_ID)
    # Nothing on disk -> os.lstat(self.path) raises, treated as foreign.
    assert snap._has_foreign_entries() is True


@pytest.mark.asyncio
async def test_delete_falls_back_to_sudo_on_eacces(cache_base, monkeypatch):
    snap = HuggingFaceSnapshot(chute_id=CHUTE_ID)
    _make_tree(snap)

    def boom(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(shutil, "rmtree", boom)

    calls = []

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"", b"")

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return FakeProc()

    monkeypatch.setattr(
        "sek8s.system_manager.cache.manager.asyncio.create_subprocess_exec",
        fake_exec,
    )

    await snap.delete()

    assert calls, "privileged remove fallback was not invoked for EACCES"
    # Must shell out to the path-restricted wrapper, never bare `rm`.
    assert calls[0][0] == "sudo"
    assert calls[0][1] == "/usr/local/bin/cache-rm"
    assert "rm" not in calls[0]


@pytest.mark.asyncio
async def test_delete_reraises_non_permission_errors(cache_base, monkeypatch):
    snap = HuggingFaceSnapshot(chute_id=CHUTE_ID)
    _make_tree(snap)

    def boom(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(shutil, "rmtree", boom)

    with pytest.raises(OSError) as exc:
        await snap.delete()
    assert exc.value.errno == errno.ENOSPC


@pytest.mark.asyncio
async def test_start_download_requires_force_for_foreign_tree(cache_base, monkeypatch):
    snap = HuggingFaceSnapshot(chute_id=CHUTE_ID)
    _make_tree(snap)
    monkeypatch.setattr(snap, "_has_foreign_entries", lambda: True)

    deleted = []

    async def fake_delete():
        deleted.append(True)

    monkeypatch.setattr(snap, "delete", fake_delete)

    with pytest.raises(ValueError, match="force=true"):
        await snap.start_download("org/repo", "main", force=False)
    assert not deleted, "must not clear a foreign tree without force"
    assert snap.path.exists()


@pytest.mark.asyncio
async def test_start_download_force_clears_foreign_tree(cache_base, monkeypatch):
    snap = HuggingFaceSnapshot(chute_id=CHUTE_ID)
    _make_tree(snap)
    monkeypatch.setattr(snap, "_has_foreign_entries", lambda: True)

    deleted = []

    async def fake_delete():
        deleted.append(True)
        shutil.rmtree(snap.path)

    monkeypatch.setattr(snap, "delete", fake_delete)

    async def fake_total(repo_id, revision):
        return 0

    monkeypatch.setattr(
        "sek8s.system_manager.cache.manager.fetch_repo_total_size", fake_total
    )

    class FakeDownloadProcess:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            return None

    monkeypatch.setattr(
        "sek8s.system_manager.cache.manager.DownloadProcess", FakeDownloadProcess
    )

    await snap.start_download("org/repo", "main", force=True)
    assert deleted == [True], "force must clear the foreign tree"
    assert snap.path.exists(), "a fresh, owned dir should be recreated"


@pytest.mark.asyncio
async def test_delete_refuses_path_outside_cache_base(cache_base, monkeypatch):
    # A chute_id that resolves outside cache_base (via symlink) must be refused
    # before any rmtree / sudo rm runs.
    outside = cache_base.parent / "outside-target"
    outside.mkdir()
    snap = HuggingFaceSnapshot(chute_id=CHUTE_ID)
    snap.path.parent.mkdir(parents=True, exist_ok=True)
    snap.path.symlink_to(outside, target_is_directory=True)

    called = []
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: called.append("rmtree"))

    with pytest.raises(PermissionError):
        await snap.delete()
    assert not called, "rmtree must not run for a path outside cache_base"
    assert outside.exists()


@pytest.mark.asyncio
async def test_delete_refuses_cache_base_itself(tmp_path, monkeypatch):
    # An empty chute_id makes self.path == cache_base; relative_to() would accept
    # it as ".", so the direct-child guard must reject it.
    monkeypatch.setattr(cache_config, "cache_base", str(tmp_path))
    snap = HuggingFaceSnapshot(chute_id="")

    called = []
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: called.append("rmtree"))

    with pytest.raises(PermissionError):
        await snap.delete()
    assert not called, "rmtree must not run against cache_base itself"
