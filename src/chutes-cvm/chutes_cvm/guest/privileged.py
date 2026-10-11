"""Running a bundled privileged helper, and the error when one fails.

Per AGENT.md's bash-vs-Python rule, Python owns the decisions and bash still owns the sequences of
privileged tool calls (cryptsetup/qemu-nbd/mkfs, ip/iptables). ``run`` exists because every one of
those calls shares an invariant worth stating once: the helper runs with the package's
``scripts/`` dir as its working directory -- they reference sibling ``volumes/``/``network/``
scripts and create volumes with relative default names, exactly as the former quick-launch.sh did
-- and a non-zero exit is a launch failure, not a warning.

``LaunchError`` lives beside it because that is what every caller raises, and because the modules
that raise it (volumes, images, network) are the ones ``launch`` imports -- so it cannot live
there without a cycle.
"""

from chutes_cvm import proc
from chutes_cvm.paths import SCRIPTS_DIR


class LaunchError(Exception):
    """A launch precondition failed (message is user-facing)."""


def run(cmd: "list[str]") -> None:
    """Run a privileged step from the scripts working directory; raise LaunchError on failure."""
    print(f"  $ {' '.join(cmd)}")
    if proc.run(cmd, cwd=str(SCRIPTS_DIR)).returncode != 0:
        raise LaunchError(f"command failed: {' '.join(cmd)}")
