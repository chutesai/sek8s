"""The initramfs must never answer a failed boot with a root shell on the console.

panic() spawns `sh -i` whenever mountroot gives up, unless `panic=` is set. Two guards
have to hold — the prod cmdline sets panic=, and the prod unlock script powers off rather
than falling through to mountroot. Either alone leaves the hole open. The debug image is
deliberately the opposite and must stay that way.
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INITRAMFS = REPO / "ansible/guest/roles/prepare-boot-image/files/initramfs"
UNLOCK = INITRAMFS / "fetch_key_and_unlock"
UNLOCK_DEBUG = INITRAMFS / "fetch_key_and_unlock_debug"
DISABLE_CONSOLE = REPO / "ansible/guest/roles/disable-console/tasks/main.yml"


@pytest.fixture(scope="module")
def unlock_lines() -> list[str]:
    return UNLOCK.read_text().splitlines()


def _index_of(lines: list[str], predicate) -> int:
    for i, line in enumerate(lines):
        if predicate(line):
            return i
    raise AssertionError("no matching line found")


def test_prod_cmdline_disallows_the_initramfs_shell():
    """Any non-empty panic= disables panic()'s shell; 0 selects halt over a reboot loop."""
    line = next(
        line
        for line in DISABLE_CONSOLE.read_text().splitlines()
        # the `line:` value, not the `regexp:` that selects it
        if 'GRUB_CMDLINE_LINUX="' in line and not line.lstrip().startswith("#")
    )
    assert "panic=0" in line, (
        "panic= missing from the prod cmdline; a failed mountroot drops to a root shell, "
        "which systemd.mask= cannot prevent because systemd never runs in that state"
    )
    # The systemd-side masks cover the post-pivot console and are still required.
    assert "systemd.mask=emergency.service" in line
    assert "systemd.mask=rescue.service" in line


def test_prod_unlock_is_fail_closed_before_it_sources_anything(unlock_lines):
    """`.` on a missing file exits a POSIX shell, so the trap must precede the sources."""
    trap_at = _index_of(unlock_lines, lambda line: line.startswith("trap fail_closed"))
    first_source_at = _index_of(
        unlock_lines, lambda line: line.startswith(". /scripts/")
    )
    assert (
        trap_at < first_source_at
    ), "trap installed after the first `.` source; a missing helper would exit before it"


def test_the_fail_closed_trap_is_below_the_prereqs_case(unlock_lines):
    """initramfs-tools runs the script with `prereqs` at build time; that exit 0 must
    not power anything off."""
    prereqs_at = _index_of(
        unlock_lines, lambda line: line.startswith("case $1 in prereqs)")
    )
    trap_at = _index_of(unlock_lines, lambda line: line.startswith("trap fail_closed"))
    assert prereqs_at < trap_at


def test_the_fail_closed_trap_powers_the_vm_off(unlock_lines):
    body = "\n".join(unlock_lines)
    handler = body.split("fail_closed() {", 1)[1].split("\n}", 1)[0]
    assert "poweroff -f" in handler
    # Must not fire once the unlock has succeeded and the richer trap has taken over.
    assert "SUCCESS_FLAG" in handler


def test_config_validation_cannot_exit_before_the_trap(unlock_lines):
    """The TDX_BASE_URL check is the exit that actually fired in the field."""
    trap_at = _index_of(unlock_lines, lambda line: line.startswith("trap fail_closed"))
    check_at = _index_of(unlock_lines, lambda line: 'if [ -z "$TDX_BASE_URL" ]' in line)
    assert trap_at < check_at


def test_debug_unlock_stays_fail_open():
    """Inverse guard: harmonising debug with prod would remove the only build you can
    debug a broken boot on."""
    text = UNLOCK_DEBUG.read_text()
    assert "fail-open" in text
    assert (
        "poweroff" not in text
    ), "debug unlock grew a poweroff; it is deliberately fail-open"
