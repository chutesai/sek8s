"""Host Fabric Manager pin must match the guest's NVIDIA driver pin.

FM talks to GPU firmware shared between host and guest, and NVIDIA requires FM and driver
to be the same x.y.z, so a skew causes NVLink init failures and Xid errors.

Only the UPSTREAM version is compared, not the full package string. Host and guest are
different Ubuntu releases drawing from different repos -- the guest is 24.04 installing
`nvidia-fabricmanager` from NVIDIA's ubuntu2404 CUDA repo, the host is 26.04 installing
`nvidia-fabricmanager-<branch>` -- so the Debian revision suffix (-1ubuntu1 vs
-0ubuntu0.26.04.N) legitimately differs and must not be asserted equal.

The two pins live in different domains (a Python constant in the chutes-cvm CLI, an Ansible
var in the guest build) tied together only by a comment, which is how the host pin drifted
to a 595.71.05-0ubuntu0.26.04.1 revision that was never published. That break is invisible
until a B200/B300 host runs `chutes-cvm host setup` -- the only hardware reaching the FM
step -- so nothing in CI or on an H200 fleet would surface it.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from chutes_cvm.host.setup import FM_PKG_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
GUEST_VARS = REPO_ROOT / "ansible/guest/playbooks/group_vars/all.yml"


def _guest_nvidia_pkg_version() -> str:
    return yaml.safe_load(GUEST_VARS.read_text())["nvidia_pkg_version"]


def _upstream(pkg_version: str) -> str:
    """The x.y.z upstream version, dropping the release-specific Debian revision."""
    return pkg_version.split("-", 1)[0]


def test_fm_upstream_version_matches_guest_driver():
    host, guest = _upstream(FM_PKG_VERSION), _upstream(_guest_nvidia_pkg_version())
    assert host == guest, (
        f"Host Fabric Manager upstream version ({host}, from FM_PKG_VERSION="
        f"{FM_PKG_VERSION!r}) does not match the guest NVIDIA driver ({guest}, from "
        f"nvidia_pkg_version in {GUEST_VARS.relative_to(REPO_ROOT)}).\n"
        "NVIDIA requires Fabric Manager and driver to be the same x.y.z. Whichever side "
        "moves, the other must move with it -- and confirm the chosen version is actually "
        "published for that side's Ubuntu release before pinning it."
    )
