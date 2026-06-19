"""Guard: audit finding IDs must never appear in public-bound (non-sensitive) files.

The dual-repo split keeps the existence/number/nature of security findings out of the
public repo. The finding<->test mapping lives only in the audit doc (a sensitive path);
public tests and production code carry no finding-ID annotations. This test fails if any
finding ID has leaked into a file that would be extracted to the public repo.

(The matcher is built from a non-literal pattern so this guard file itself contains no
finding ID and does not self-trigger.)
"""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_FINDING_ID = re.compile(r"SEK8S-\d{3}")


def _sensitive_exclusions():
    spec = (REPO / ".security-sensitive-paths").read_text().splitlines()
    paths = [
        ln.strip().rstrip("/")
        for ln in spec
        if ln.strip() and not ln.strip().startswith("#")
    ]
    return [f":(exclude){p}" for p in paths]


def test_no_finding_ids_in_public_bound_files():
    files = subprocess.run(
        ["git", "ls-files", "--", ".", *_sensitive_exclusions()],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    offenders = []
    for rel in files:
        try:
            text = (REPO / rel).read_text(errors="ignore")
        except OSError:
            continue
        if _FINDING_ID.search(text):
            offenders.append(rel)
    assert not offenders, (
        "audit finding IDs must not appear in public-bound files — keep the "
        "finding/test mapping in the audit doc. Offending files: "
        + ", ".join(sorted(offenders))
    )
