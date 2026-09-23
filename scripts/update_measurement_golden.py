#!/usr/bin/env python3
"""Regenerate the measurement goldens in tests/measurement/golden/.

Run by hand, never from the test suite. A golden is the byte-exact input the tdx-measure fork
hashes, so a change to one means every published measurement for that hardware class is wrong and
those hosts stop attesting.

    python scripts/update_measurement_golden.py          # rewrite, then review `git diff`
    python scripts/update_measurement_golden.py --check  # report drift, write nothing (CI)

The workflow for an intended change is: run it, read the diff, confirm ONLY what you meant to
change moved, and republish the real measurements in the same rollout.
"""

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# chutes-cvm is not installed into the venv; tests/measurement/conftest.py puts it on sys.path,
# and pytest puts tests/ there. Standalone we do both ourselves.
sys.path[:0] = [
    str(REPO_ROOT / "src" / "chutes-cvm"),
    str(REPO_ROOT / "tests"),
    str(REPO_ROOT / "tests" / "measurement"),
]

from golden_cases import CASES, GOLDEN_DIR, golden_path, snapshot  # noqa: E402


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--check",
        action="store_true",
        help="report which goldens would change and exit non-zero; write nothing",
    )
    args = ap.parse_args(argv)

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    changed, created = [], []
    for name in sorted(CASES):
        path = golden_path(name)
        # The topology builders narrate to stdout for an operator mid-launch; here it buries
        # the one line that matters.
        with contextlib.redirect_stdout(io.StringIO()):
            payload = json.dumps(snapshot(name), indent=2) + "\n"
        if not path.exists():
            created.append(name)
        elif path.read_text() == payload:
            continue
        else:
            changed.append(name)
        if not args.check:
            path.write_text(payload)

    for name in created:
        print(f"  new      {golden_path(name).relative_to(REPO_ROOT)}")
    for name in changed:
        print(f"  CHANGED  {golden_path(name).relative_to(REPO_ROOT)}")

    if not (created or changed):
        print(f"{len(CASES)} goldens already current; nothing written.")
        return 0

    if args.check:
        print(
            f"\n{len(created) + len(changed)} golden(s) differ from the code. "
            "This is a measurement change.",
            file=sys.stderr,
        )
        return 1

    print(
        f"\nWrote {len(created) + len(changed)} of {len(CASES)}. Now read `git diff "
        f"{GOLDEN_DIR.relative_to(REPO_ROOT)}` and confirm only the intended change moved.\n"
        "A changed golden means published measurements must be regenerated in the same rollout."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
