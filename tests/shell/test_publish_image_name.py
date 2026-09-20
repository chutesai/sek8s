"""publish-image.sh names R2 objects; a wrong name overwrites what the fleet boots.

Driven against a staged copy of the repo layout (the script derives REPO_ROOT from its own
path) with rclone and python3 stubbed, so nothing touches a real bucket.
"""

import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT_SRC = REPO / "guest-tools/scripts/publish-image.sh"
ARTIFACTS = ("qcow2", "vmlinuz", "initrd", "cmdline")
VERSION = "1.4.1"


@pytest.fixture
def publish(tmp_path):
    repo = tmp_path / "repo"
    (repo / "guest-tools/scripts").mkdir(parents=True)
    (repo / "ansible/guest").mkdir(parents=True)
    (repo / "ansible/guest/VERSION").write_text(f"{VERSION}\n")
    script = repo / "guest-tools/scripts/publish-image.sh"
    script.write_text(SCRIPT_SRC.read_text())
    script.chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "rclone.log"
    (bin_dir / "python3").write_text("#!/usr/bin/env bash\nexit 0\n")
    (bin_dir / "python3").chmod(0o755)

    def _rclone(listing: str, lsf_rc: int) -> None:
        """Stub rclone: log every call, and answer `lsf` with a fixed listing/exit code."""
        (bin_dir / "rclone").write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$@" >> {shlex.quote(str(log))}\n'
            f'if [ "$1" = lsf ]; then printf "%s" {shlex.quote(listing)}; exit {lsf_rc}; fi\n'
            "exit 0\n"
        )
        (bin_dir / "rclone").chmod(0o755)

    def run(*args, existing: str = "", suffix: str = "", lsf_rc: int = 0):
        _rclone(existing, lsf_rc)
        image_dir = repo / "guest-tools/image/prod" / f"{VERSION}{suffix}"
        image_dir.mkdir(parents=True, exist_ok=True)
        for ext in ARTIFACTS:
            (image_dir / f"{VERSION}{suffix}.{ext}").write_text("x")
        result = subprocess.run(
            ["bash", str(script), *args],
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
            capture_output=True,
            text=True,
        )
        result.rclone = log.read_text() if log.exists() else ""  # type: ignore[attr-defined]
        return result

    return run


BUCKET = "r2:chutes-tdx/"


def _uploaded(result) -> set[str]:
    """Destination keys (bucket stripped) from the stubbed copyto calls."""
    return {
        line.split()[-1].removeprefix(BUCKET)
        for line in result.rclone.splitlines()
        if line.startswith("copyto")
    }


def test_default_name_is_unchanged(publish):
    result = publish()
    assert result.returncode == 0, result.stderr
    assert _uploaded(result) == {f"tdx-guest.{ext}" for ext in ARTIFACTS} | {
        "tdx-guest.manifest.json"
    }


def test_name_override_retargets_every_artifact(publish):
    result = publish("--name", "1.4.0")
    assert result.returncode == 0, result.stderr
    assert _uploaded(result) == {f"1.4.0.{ext}" for ext in ARTIFACTS} | {
        "1.4.0.manifest.json"
    }
    assert not any("tdx-guest" in name for name in _uploaded(result))


def test_debug_suffix_composes_with_the_override(publish):
    result = publish("--name", "1.4.0", "--debug", suffix="-debug")
    assert result.returncode == 0, result.stderr
    assert _uploaded(result) == {f"1.4.0-debug.{ext}" for ext in ARTIFACTS} | {
        "1.4.0-debug.manifest.json"
    }


@pytest.mark.parametrize("bad", ["a/b", "../tdx-guest", "-lead", ".hidden", ""])
def test_a_name_that_is_not_one_path_segment_is_rejected(publish, bad):
    """A '/' would publish under a different prefix than the script reports."""
    result = publish("--name", bad)
    assert result.returncode == 2
    assert "--name must be one path segment" in result.stderr


def test_an_archival_name_will_not_clobber_an_existing_set(publish):
    result = publish("--name", "1.4.0", existing="1.4.0.qcow2\n1.4.0.initrd\n")
    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert not _uploaded(result), "refused publish still uploaded something"


def test_force_overrides_the_archival_guard(publish):
    result = publish("--name", "1.4.0", "--force", existing="1.4.0.qcow2\n")
    assert result.returncode == 0, result.stderr
    assert f"1.4.0.{ARTIFACTS[0]}" in _uploaded(result)


def test_the_default_name_still_overwrites(publish):
    """The canonical set is replaced every release; guarding it would break publishing."""
    result = publish(existing="tdx-guest.qcow2\n")
    assert result.returncode == 0, result.stderr
    assert "tdx-guest.qcow2" in _uploaded(result)


def test_prefix_publishes_into_a_subdirectory_keeping_the_basename(publish):
    """One bucket entry per version, with canonical names inside, so restoring an archive
    is a copy back to the root rather than a rename."""
    result = publish("--prefix", "tdx-guest-1.4.0")
    assert result.returncode == 0, result.stderr
    assert _uploaded(result) == {
        f"tdx-guest-1.4.0/tdx-guest.{ext}" for ext in ARTIFACTS
    } | {"tdx-guest-1.4.0/tdx-guest.manifest.json"}


def test_prefix_composes_with_debug_and_name(publish):
    result = publish(
        "--prefix", "archive/1.4.0", "--name", "img", "--debug", suffix="-debug"
    )
    assert result.returncode == 0, result.stderr
    assert "archive/1.4.0/img-debug.qcow2" in _uploaded(result)


def test_a_trailing_slash_on_prefix_is_tolerated(publish):
    result = publish("--prefix", "tdx-guest-1.4.0/")
    assert result.returncode == 0, result.stderr
    assert "tdx-guest-1.4.0/tdx-guest.qcow2" in _uploaded(result)


@pytest.mark.parametrize("bad", ["../escape", "a//b", "-lead/x", "/abs", "a//"])
def test_prefix_is_validated_per_segment(publish, bad):
    result = publish("--prefix", bad)
    assert result.returncode == 2
    assert "--prefix must be" in result.stderr


def test_prefix_alone_triggers_the_archival_guard(publish):
    """The basename is still the default here, so the guard must key off the target, not
    just off --name."""
    result = publish("--prefix", "tdx-guest-1.4.0", existing="tdx-guest.qcow2\n")
    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert not _uploaded(result)


def test_a_failed_listing_does_not_pass_for_an_empty_one(publish):
    """A password-protected rclone config makes `lsf` fail; treating that as "nothing
    there" would let an archive be overwritten by the very check meant to prevent it."""
    result = publish("--prefix", "tdx-guest-1.4.0", lsf_rc=2)
    assert result.returncode == 1
    assert "cannot list" in result.stderr
    assert not _uploaded(result)


def test_a_missing_prefix_is_not_a_listing_failure(publish):
    """R2 has no real directories, so the first archive under a new prefix makes rclone
    exit 3 ("directory not found"). Treating that as an error would block exactly the
    publish the guard exists to protect."""
    result = publish(
        "--prefix",
        "tdx-guest-1.4.0",
        existing="ERROR : : error listing: directory not found",
        lsf_rc=3,
    )
    assert result.returncode == 0, result.stderr
    assert "tdx-guest-1.4.0/tdx-guest.qcow2" in _uploaded(result)
