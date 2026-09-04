"""Static checks on the guest AppArmor profiles.

These run the real `apparmor_parser` over each profile (skipped where the binary
is absent) and pin the one rule whose absence broke boot: `sek8s.deny-sensitive-default`
auto-attaches to /usr/bin/bash, so every k3s cluster-init step — which the post-start
wrapper invokes as `bash <script>` — runs confined by it. Under `abi <abi/4.0>` D-Bus
method calls are mediated separately from the socket, so without explicit dbus rules
`systemctl is-active k3s` inside a step failed with "Failed to connect to bus:
Permission denied" while the same call in the unconfined wrapper succeeded.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Template

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "ansible/guest/roles/apparmor-hardening"
PROFILE_DIR = ROLE / "files/profiles"
ABSTRACTION_DIR = ROLE / "templates/abstractions"

PROFILES = [
    "sek8s.system-manager",
    "sek8s.setup-cache",
    "sek8s.deny-sensitive-default",
    "sek8s.attestation-proxy",
    "sek8s.chute-log-shipper",
]


@pytest.fixture(scope="module")
def include_dir(tmp_path_factory):
    """Render the Jinja abstractions so the parser can resolve the includes."""
    root = tmp_path_factory.mktemp("apparmor")
    out = root / "abstractions"
    out.mkdir()
    for name in ("sek8s-cache-deny", "sek8s-secrets-deny"):
        template = Template((ABSTRACTION_DIR / f"{name}.j2").read_text())
        (out / name).write_text(template.render(debug_build=False))
    return root


@pytest.mark.parametrize("profile", PROFILES)
def test_profile_parses(profile, include_dir):
    parser = shutil.which("apparmor_parser")
    if parser is None:
        pytest.skip("apparmor_parser not available")

    result = subprocess.run(
        [parser, "-Q", "-K", "-I", str(include_dir), str(PROFILE_DIR / profile)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_verifier_covers_every_installed_profile():
    """The boot verifier powers off on a missing profile — keep its list in sync."""
    verifier = (ROLE / "files/verify-apparmor-profiles.sh").read_text()
    for profile in PROFILES:
        assert profile in verifier


def test_default_profile_allows_systemd_dbus():
    """Confined shells must be able to run `systemctl is-active`.

    The k3s cluster-init steps depend on it; denying it restart-looped the VM.
    """
    profile = (PROFILE_DIR / "sek8s.deny-sensitive-default").read_text()
    assert "peer=(name=org.freedesktop.systemd1)" in profile
    assert "peer=(name=org.freedesktop.DBus)" in profile


def test_default_profile_still_denies_the_sensitive_paths():
    """The D-Bus grant must not have widened what this profile exists to block."""
    profile = (PROFILE_DIR / "sek8s.deny-sensitive-default").read_text()
    assert "include <abstractions/sek8s-cache-deny>" in profile
    assert "include <abstractions/sek8s-secrets-deny>" in profile
    for capability in ("sys_module", "mac_admin", "mac_override"):
        assert f"deny capability {capability}," in profile
