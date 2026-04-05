#!/usr/bin/env python3
"""
Sync version from each package's VERSION file into its pyproject.toml [tool.poetry] section.
VERSION is the source of truth; pyproject.toml is updated to match.

Usage:
    python sync_pyproject_versions.py          # fix mode: update pyproject.toml files in-place
    python sync_pyproject_versions.py --check  # check mode: exit 1 if any mismatch found
"""
import argparse
from pathlib import Path
import re
import sys

SRC_DIR = Path(__file__).resolve().parent.parent / "src"

VERSION_PATTERN = re.compile(r'^version\s*=\s*["\']?([^"\']*)["\']?\s*$')


def get_version_from_file(version_path: Path) -> str:
    """First line of VERSION, stripped."""
    text = version_path.read_text().strip()
    return text.splitlines()[0].strip() if text else ""


def get_pyproject_version(pyproject: Path) -> str:
    """Read the [tool.poetry] version from pyproject.toml."""
    in_poetry = False
    for line in pyproject.read_text().splitlines():
        stripped = line.strip()
        if stripped == "[tool.poetry]":
            in_poetry = True
        elif in_poetry and stripped.startswith("["):
            break
        elif in_poetry:
            m = VERSION_PATTERN.match(stripped)
            if m:
                return m.group(1)
    return ""


def check_pyproject(module_dir: Path) -> bool:
    """Return True if VERSION and pyproject.toml are in sync."""
    version_file = module_dir / "VERSION"
    pyproject = module_dir / "pyproject.toml"
    if not version_file.is_file() or not pyproject.is_file():
        return True

    version = get_version_from_file(version_file)
    if not version:
        return True

    return get_pyproject_version(pyproject) == version


def sync_pyproject(module_dir: Path) -> bool:
    """Update pyproject.toml version to match VERSION. Returns True if file was changed."""
    version_file = module_dir / "VERSION"
    pyproject = module_dir / "pyproject.toml"
    if not version_file.is_file() or not pyproject.is_file():
        return False

    version = get_version_from_file(version_file)
    if not version:
        return False

    content = pyproject.read_text()
    in_poetry = False
    new_lines = []
    changed = False

    for line in content.splitlines(keepends=True):
        if line.strip() == "[tool.poetry]":
            in_poetry = True
        elif in_poetry and line.strip().startswith("["):
            in_poetry = False

        if in_poetry and VERSION_PATTERN.match(line.strip()):
            new_line = f'version = "{version}"\n'
            if line.rstrip() != new_line.rstrip():
                changed = True
                line = new_line
        new_lines.append(line)

    if changed:
        pyproject.write_text("".join(new_lines))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: exit 1 if any VERSION/pyproject.toml mismatch is found (no files modified).",
    )
    args = parser.parse_args()

    if args.check:
        mismatches = []
        for path in sorted(SRC_DIR.iterdir()):
            if path.is_dir() and not check_pyproject(path):
                mismatches.append(path.name)
        if mismatches:
            for name in mismatches:
                print(f"MISMATCH: {name}/VERSION does not match {name}/pyproject.toml")
            print("Run: python scripts/sync_pyproject_versions.py")
            return 1
        print("All packages: VERSION and pyproject.toml in sync.")
        return 0

    updated = []
    for path in sorted(SRC_DIR.iterdir()):
        if path.is_dir() and sync_pyproject(path):
            updated.append(path.name)

    if updated:
        for name in updated:
            print(f"Updated {name}: pyproject.toml version synced from VERSION")
        return 0
    print("All packages already in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
