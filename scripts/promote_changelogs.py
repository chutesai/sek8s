#!/usr/bin/env python3
"""
Fragment-based changelog promotion.

Developers drop categorized .md fragments into changelogs/<component>/unreleased/.
This script validates or promotes those fragments into CHANGELOG.md.

Usage:
    python promote_changelogs.py --check   --version-files ansible/k3s/VERSION
    python promote_changelogs.py --promote --version-files ansible/k3s/VERSION src/sek8s/VERSION
"""
import argparse
from collections import OrderedDict
from datetime import date
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent

VERSION_CHANGELOG_MAP = {
    "ansible/k3s/VERSION": "changelogs/vm",
    "src/sek8s/VERSION": "changelogs/sek8s",
    "src/attestation-proxy/VERSION": "changelogs/attestation-proxy",
}

CATEGORY_ORDER = ["Added", "Changed", "Fixed", "Removed"]

HEADING_RE = re.compile(r"^## \[(.+?)\]")
CATEGORY_RE = re.compile(r"^### (.+)")


def read_version(version_file: Path) -> str:
    text = version_file.read_text().strip()
    return text.splitlines()[0].strip() if text else ""


def changelog_has_version(changelog: Path, version: str) -> bool:
    if not changelog.is_file():
        return False
    for line in changelog.read_text().splitlines():
        m = HEADING_RE.match(line)
        if m and m.group(1) == version:
            return True
    return False


def collect_fragments(unreleased_dir: Path) -> list[Path]:
    if not unreleased_dir.is_dir():
        return []
    return sorted(
        p for p in unreleased_dir.iterdir()
        if p.suffix == ".md" and p.name != ".gitkeep"
    )


def parse_fragment(path: Path) -> OrderedDict[str, list[str]]:
    """Parse a fragment file into {category: [bullet lines]}."""
    categories: OrderedDict[str, list[str]] = OrderedDict()
    current_cat = None
    for line in path.read_text().splitlines():
        cat_match = CATEGORY_RE.match(line)
        if cat_match:
            current_cat = cat_match.group(1)
            if current_cat not in categories:
                categories[current_cat] = []
        elif current_cat is not None and line.strip():
            categories[current_cat].append(line)
    return categories


def aggregate_fragments(fragments: list[Path]) -> OrderedDict[str, list[str]]:
    """Merge all fragments, grouping bullets by category in standard order."""
    merged: dict[str, list[str]] = {}
    for frag in fragments:
        parsed = parse_fragment(frag)
        for cat, bullets in parsed.items():
            merged.setdefault(cat, []).extend(bullets)

    ordered: OrderedDict[str, list[str]] = OrderedDict()
    for cat in CATEGORY_ORDER:
        if cat in merged:
            ordered[cat] = merged.pop(cat)
    for cat in sorted(merged.keys()):
        ordered[cat] = merged[cat]
    return ordered


def build_version_section(version: str, categories: OrderedDict[str, list[str]]) -> str:
    lines = [f"## [{version}] - {date.today().isoformat()}", ""]
    for cat, bullets in categories.items():
        lines.append(f"### {cat}")
        lines.extend(bullets)
        lines.append("")
    return "\n".join(lines)


def find_insert_position(content: str) -> int:
    """Find the byte offset after the preamble where versioned entries start."""
    lines = content.splitlines(keepends=True)
    offset = 0
    for line in lines:
        if HEADING_RE.match(line):
            return offset
        offset += len(line)
    return offset


def promote(changelog: Path, version_section: str) -> None:
    content = changelog.read_text() if changelog.is_file() else ""
    pos = find_insert_position(content)
    new_content = content[:pos] + version_section + "\n" + content[pos:]
    changelog.write_text(new_content)


def check_mode(version_files: list[str]) -> int:
    errors = 0
    for vf_str in version_files:
        vf = REPO_ROOT / vf_str
        comp_dir_str = VERSION_CHANGELOG_MAP.get(vf_str)
        if comp_dir_str is None:
            continue
        comp_dir = REPO_ROOT / comp_dir_str

        version = read_version(vf)
        if not version:
            print(f"ERROR: {vf_str} is empty")
            errors += 1
            continue

        changelog = comp_dir / "CHANGELOG.md"
        if changelog_has_version(changelog, version):
            print(f"ERROR: {changelog.relative_to(REPO_ROOT)} already has ## [{version}]. "
                  "Versioned headings are created by automation only.")
            errors += 1
            continue

        fragments = collect_fragments(comp_dir / "unreleased")
        if not fragments:
            print(f"ERROR: No changelog fragments in {comp_dir.relative_to(REPO_ROOT)}/unreleased/. "
                  f"Add a .md fragment describing changes for {vf_str} bump to {version}.")
            errors += 1
        else:
            names = ", ".join(f.name for f in fragments)
            print(f"OK: {comp_dir.relative_to(REPO_ROOT)}/unreleased/ has fragments: {names}")

    return 1 if errors else 0


def promote_mode(version_files: list[str]) -> int:
    promoted = []
    for vf_str in version_files:
        vf = REPO_ROOT / vf_str
        comp_dir_str = VERSION_CHANGELOG_MAP.get(vf_str)
        if comp_dir_str is None:
            continue
        comp_dir = REPO_ROOT / comp_dir_str

        version = read_version(vf)
        if not version:
            print(f"SKIP: {vf_str} is empty")
            continue

        changelog = comp_dir / "CHANGELOG.md"
        if changelog_has_version(changelog, version):
            print(f"SKIP: {changelog.relative_to(REPO_ROOT)} already has ## [{version}]")
            continue

        unreleased_dir = comp_dir / "unreleased"
        fragments = collect_fragments(unreleased_dir)
        if not fragments:
            print(f"SKIP: No fragments in {unreleased_dir.relative_to(REPO_ROOT)}")
            continue

        categories = aggregate_fragments(fragments)
        section = build_version_section(version, categories)
        promote(changelog, section)

        for frag in fragments:
            frag.unlink()

        promoted.append(f"{comp_dir.relative_to(REPO_ROOT)}: [{version}] from {len(fragments)} fragment(s)")

    if promoted:
        for msg in promoted:
            print(f"Promoted {msg}")
    else:
        print("Nothing to promote.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="Validate fragments exist and versioned heading does not.")
    group.add_argument("--promote", action="store_true",
                       help="Aggregate fragments into CHANGELOG.md and delete them.")
    parser.add_argument("--version-files", nargs="+", required=True,
                        help="VERSION files that were bumped (relative to repo root).")
    args = parser.parse_args()

    if args.check:
        return check_mode(args.version_files)
    else:
        return promote_mode(args.version_files)


if __name__ == "__main__":
    sys.exit(main())
