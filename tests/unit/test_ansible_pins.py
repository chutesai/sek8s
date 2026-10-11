"""Every file the guest build downloads is pinned by SHA-256, not just by version.

The guest build fetches a few tools and repository keys from upstream on every build, and they
land in the measured image (or are the trust roots apt verifies packages against). A version
pins what we asked for; only a checksum pins what was served, so a changed or tampered upstream
asset fails the build instead of being measured into the release.
"""

import re
from pathlib import Path

import pytest
import yaml

ANSIBLE = Path(__file__).resolve().parents[2] / "ansible" / "guest"
TASK_FILES = sorted(
    [*(ANSIBLE / "roles").glob("*/tasks/*.yml"), *(ANSIBLE / "playbooks").glob("*.yml")]
)
GET_URL = ("ansible.builtin.get_url", "get_url")


def _tasks(node):
    """Every task dict in a parsed task or playbook file, including those nested in blocks and
    in a play's task lists."""
    if isinstance(node, list):
        for item in node:
            yield from _tasks(item)
    elif isinstance(node, dict):
        yield node
        for key in ("block", "rescue", "always", "pre_tasks", "tasks", "post_tasks"):
            yield from _tasks(node.get(key) or [])


def _downloads():
    for path in TASK_FILES:
        for task in _tasks(yaml.safe_load(path.read_text()) or []):
            for module in GET_URL:
                if module in task:
                    yield path.relative_to(ANSIBLE), task.get("name", "?"), task[module]


def _defined_vars():
    """Variables defined in role defaults and group_vars, with their values."""
    files = [*(ANSIBLE / "roles").glob("*/defaults/main.yml")]
    files += (ANSIBLE / "playbooks" / "group_vars").glob("*.yml")
    defined = {}
    for path in files:
        defined.update(yaml.safe_load(path.read_text()) or {})
    return defined


DOWNLOADS = list(_downloads())


def test_the_build_has_downloads_to_check():
    assert len(DOWNLOADS) >= 8


@pytest.mark.parametrize("where, name, args", DOWNLOADS, ids=lambda x: str(x)[:60])
def test_every_download_is_pinned_by_sha256(where, name, args):
    checksum = str(args.get("checksum", ""))
    assert checksum.startswith("sha256:"), f"{where}: '{name}' has no sha256 checksum"
    var = re.fullmatch(r"sha256:\{\{\s*(\w+)\s*\}\}", checksum)
    assert (
        var
    ), f"{where}: '{name}' checksum is not a single pinned variable: {checksum}"
    if var.group(1) == "source_image_sha256":
        return  # the build's source image, pinned per playbook (fetch-image)
    value = str(_defined_vars().get(var.group(1), ""))
    assert re.fullmatch(
        r"[0-9a-f]{64}", value
    ), f"{var.group(1)} is not a pinned digest"


def test_the_k3s_binary_is_checked_against_its_pin():
    """The installer checks the binary only against the release's own sha256sum file."""
    install = yaml.safe_load((ANSIBLE / "roles/k3s/tasks/install.yml").read_text())
    asserts = [
        t["ansible.builtin.assert"]
        for t in _tasks(install)
        if "ansible.builtin.assert" in t
    ]
    assert any("k3s_sha256" in str(a.get("that")) for a in asserts)
