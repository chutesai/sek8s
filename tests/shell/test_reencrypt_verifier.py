"""Tests for the secrets/configmaps at-rest encryption verifier embedded in
00-reencrypt-secrets.sh.

The verifier (`verify_resources_encrypted`) reads state.db directly and must report
a non-zero exit when ANY live secret OR configmap row is still plaintext. A prior
version checked secrets only, so a plaintext configmap could pass while the
self-validating marker was written. These tests run the actual embedded python
against sqlite fixtures so a regression (e.g. narrowing the query back to secrets
only) fails here.
"""

import re
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ansible/guest/roles/k3s/files/cluster-init/00-reencrypt-secrets.sh"

# Extract the python the script runs as `python3 - "$STATE_DB" <<'PYEOF' ... PYEOF`.
_HEREDOC = re.compile(r"<<'PYEOF'\n(.*?)\nPYEOF", re.DOTALL)


def _verifier_src() -> str:
    m = _HEREDOC.search(SCRIPT.read_text())
    assert m, "could not find the PYEOF verifier heredoc in 00-reencrypt-secrets.sh"
    return m.group(1)


def _make_state_db(tmp_path, rows):
    """rows: list of (name, value_bytes) or (name, value_bytes, deleted).

    Builds a minimal kine table including the `deleted` tombstone flag that the
    real kine schema carries. `deleted` defaults to 0 (live) when omitted.
    """
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE kine ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, "
        "deleted INTEGER DEFAULT 0, value BLOB)"
    )
    normalized = [(r[0], r[1], r[2] if len(r) > 2 else 0) for r in rows]
    conn.executemany(
        "INSERT INTO kine (name, value, deleted) VALUES (?, ?, ?)",
        [(name, value, deleted) for (name, value, deleted) in normalized],
    )
    conn.commit()
    conn.close()
    return db


def _run_verifier(db) -> int:
    # Faithful to the script: code on stdin, db as argv[1] (python3 - <db>).
    return subprocess.run(
        [sys.executable, "-", str(db)],
        input=_verifier_src(),
        text=True,
        capture_output=True,
    ).returncode


ENC = b"k8s:enc:secretbox:v1:key1:" + b"\x00\x10ciphertext"
PLAIN = b'{"apiVersion":"v1","kind":"Secret"}'


def test_all_encrypted_passes(tmp_path):
    db = _make_state_db(
        tmp_path,
        [
            ("/registry/secrets/default/a", ENC),
            ("/registry/configmaps/default/b", ENC),
        ],
    )
    assert _run_verifier(db) == 0


def test_plaintext_configmap_fails(tmp_path):
    # The regression: secret sealed, configmap plaintext -> must FAIL (exit 1).
    db = _make_state_db(
        tmp_path,
        [
            ("/registry/secrets/default/a", ENC),
            ("/registry/configmaps/default/b", PLAIN),
        ],
    )
    assert _run_verifier(db) == 1


def test_plaintext_secret_fails(tmp_path):
    db = _make_state_db(
        tmp_path,
        [
            ("/registry/secrets/default/a", PLAIN),
            ("/registry/configmaps/default/b", ENC),
        ],
    )
    assert _run_verifier(db) == 1


def test_only_latest_revision_considered(tmp_path):
    # An old plaintext row superseded by a newer encrypted row (higher id) is OK.
    db = _make_state_db(
        tmp_path,
        [
            ("/registry/secrets/default/a", PLAIN),  # old
            ("/registry/secrets/default/a", ENC),  # newer, wins
        ],
    )
    assert _run_verifier(db) == 0


def test_empty_db_passes(tmp_path):
    db = _make_state_db(tmp_path, [])
    assert _run_verifier(db) == 0


def test_deleted_plaintext_tombstone_ignored(tmp_path):
    # Regression for the fresh-volume poweroff bug: a build-time secret that was
    # created then DELETED leaves a tombstone (deleted=1) whose value is the
    # pre-encryption plaintext. The apiserver does not list deleted resources, so
    # the online re-encrypt loop can never seal it. The verifier must NOT treat it
    # as a live plaintext secret, or verification fails forever on a fresh volume.
    db = _make_state_db(
        tmp_path,
        [
            ("/registry/secrets/default/live", ENC),
            ("/registry/secrets/default/gone", PLAIN, 1),
            ("/registry/configmaps/default/gone", PLAIN, 1),
        ],
    )
    assert _run_verifier(db) == 0


def test_deleted_empty_tombstone_ignored(tmp_path):
    # A tombstone may carry a NULL/empty value rather than the prior plaintext.
    # `bytes(v or b'')` turns that into b'', which fails the secretbox substring
    # check — so without the deleted-row filter this would be a false positive.
    db = _make_state_db(
        tmp_path,
        [
            ("/registry/secrets/default/live", ENC),
            ("/registry/secrets/default/gone", None, 1),
        ],
    )
    assert _run_verifier(db) == 0


def test_live_plaintext_still_fails_when_tombstones_present(tmp_path):
    # The deleted-row filter must not mask a genuinely plaintext LIVE secret.
    db = _make_state_db(
        tmp_path,
        [
            ("/registry/secrets/default/gone", PLAIN, 1),
            ("/registry/secrets/default/live", PLAIN, 0),
        ],
    )
    assert _run_verifier(db) == 1
