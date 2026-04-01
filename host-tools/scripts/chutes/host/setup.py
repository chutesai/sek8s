"""TDX host setup orchestration.

Consumes a HostProfile and executes the setup steps: PPAs, packages,
kernel selection, GRUB configuration, and kvm group membership.
All OS-version differences are encoded in the profile — this module
contains no version-specific branching.
"""

import os
import re
import subprocess
import sys

from chutes.host.profiles import HostProfile, PPA


def _run(cmd: list[str], **kwargs):
    """Run a command, printing it first. Raises on failure."""
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, **kwargs)


def _add_ppa(ppa: PPA, codename: str):
    """Add an APT PPA and pin its packages at the configured priority.

    When ppa.suite is set, the sources entry is written manually instead of
    using add-apt-repository (which would auto-detect the wrong suite).
    """
    suite = ppa.suite or codename

    if ppa.suite:
        print(f"  Adding PPA: {ppa.uri} (pinned to suite {suite})")
        _add_ppa_manual(ppa, suite)
    else:
        print(f"  Adding PPA: {ppa.uri}")
        _run(["sudo", "add-apt-repository", "-y", ppa.uri])

    distro_id = f"LP-PPA-{ppa.team}-{ppa.name}"
    pin_file = f"/etc/apt/preferences.d/kobuk-tdx-{ppa.team}-{ppa.name}-pin-{ppa.pin_priority}"
    pin_content = (
        f"Package: *\n"
        f"Pin: release o={distro_id}\n"
        f"Pin-Priority: {ppa.pin_priority}\n"
    )
    _write_system_file(pin_file, pin_content)

    unattended_file = f"/etc/apt/apt.conf.d/99unattended-upgrades-kobuk-{ppa.name}"
    unattended_content = (
        f'Unattended-Upgrade::Allowed-Origins {{\n'
        f'  "{distro_id}:{suite}";\n'
        f'}};\n'
        f'Unattended-Upgrade::Allow-downgrade "true";\n'
    )
    _write_system_file(unattended_file, unattended_content)


def _add_ppa_manual(ppa: PPA, suite: str):
    """Write a DEB822 sources entry for a PPA with a specific suite.

    Used when add-apt-repository would pick the wrong suite (e.g. the PPA
    hasn't published packages for the running release).  Removes any stale
    sources entries for this PPA first (e.g. from a prior add-apt-repository
    that auto-detected the wrong suite).
    """
    sources_file = f"/etc/apt/sources.list.d/kobuk-{ppa.team}-{ppa.name}.sources"
    keyring_path = f"/etc/apt/keyrings/kobuk-{ppa.team}-{ppa.name}.asc"

    _remove_stale_ppa_sources(ppa)

    _run(["sudo", "mkdir", "-p", "/etc/apt/keyrings"])
    _fetch_signing_key(ppa.signing_key, keyring_path)

    sources_content = (
        f"Types: deb\n"
        f"URIs: https://ppa.launchpadcontent.net/{ppa.team}/{ppa.name}/ubuntu/\n"
        f"Suites: {suite}\n"
        f"Components: main\n"
        f"Signed-By: {keyring_path}\n"
    )
    _write_system_file(sources_file, sources_content)


def _fetch_signing_key(fingerprint: str, dest: str):
    """Download a GPG signing key from keyserver.ubuntu.com.

    Saves the ASCII-armored key directly -- modern apt (2.4+, i.e. Ubuntu
    24.04+) accepts armored keys in Signed-By without dearmoring.
    """
    url = (
        f"https://keyserver.ubuntu.com/pks/lookup"
        f"?op=get&search=0x{fingerprint}"
    )
    print(f"  Fetching signing key {fingerprint[:16]}...")
    subprocess.run(
        ["sudo", "curl", "-fsSL", "-o", dest, url],
        check=True,
    )


def _remove_stale_ppa_sources(ppa: PPA):
    """Remove any existing apt sources entries for this PPA.

    Cleans up entries left by add-apt-repository or prior manual installs
    so that our suite-pinned entry is the only one.
    """
    import glob as globmod

    patterns = [
        f"/etc/apt/sources.list.d/*{ppa.team}*{ppa.name}*",
    ]
    for pattern in patterns:
        for path in globmod.glob(pattern):
            print(f"  Removing stale PPA source: {path}")
            _run(["sudo", "rm", "-f", path])


def _write_system_file(path: str, content: str):
    """Write content to a root-owned system file via tee."""
    _run(
        ["sudo", "tee", path],
        input=content.encode(),
        stdout=subprocess.DEVNULL,
    )


def _get_kernel_version(kernel_package: str) -> str:
    """Resolve the concrete kernel version from a metapackage name.

    Parses `apt show <metapackage>` for the Depends line to extract
    the actual kernel version string (e.g. '6.17.0-15-generic').
    """
    result = subprocess.run(
        ["apt", "show", kernel_package],
        capture_output=True,
        text=True,
    )
    match = re.search(
        r"Depends:.*linux-image-([^,\s]+)",
        result.stdout,
    )
    if not match:
        raise RuntimeError(
            f"Could not determine kernel version from {kernel_package}. "
            f"apt show output:\n{result.stdout}"
        )
    return match.group(1)


def _grub_set_kernel(kernel_version: str):
    """Set the given kernel as the default boot entry via grub-editenv.

    Same logic as tdx/setup-tdx-common grub_switch_kernel() (awk + cut on
    /boot/grub/grub.cfg). Newer Ubuntu sometimes omits the Advanced options
    submenu (flat kernel list); then MID is empty and saved_entry is just KID.
    """
    print(f"  Setting default kernel: {kernel_version}")
    grub_cfg = "/boot/grub/grub.cfg"

    # MID: awk '/Advanced options for Ubuntu/{print $(NF-1)}' | cut -d\' -f2
    mid_raw = subprocess.run(
        ["awk", "/Advanced options for Ubuntu/{print $(NF-1)}", grub_cfg],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    first_mid_line = mid_raw.split("\n", 1)[0] if mid_raw else ""
    if first_mid_line:
        mid = subprocess.run(
            ["cut", "-d'", "-f2"],
            input=first_mid_line,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    else:
        mid = ""

    # KID: awk "/with Linux $KERNELVER/"'{print $(NF-1)}' | cut -d\' -f2 | head -n1
    kid_raw = subprocess.run(
        ["awk", f"/with Linux {kernel_version}/{{print $(NF-1)}}", grub_cfg],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    first_kid_line = kid_raw.split("\n", 1)[0] if kid_raw else ""
    if not first_kid_line:
        raise RuntimeError(
            f"Could not find kernel {kernel_version} in grub.cfg menu entries"
        )
    kid = subprocess.run(
        ["cut", "-d'", "-f2"],
        input=first_kid_line,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not kid:
        raise RuntimeError(
            f"Could not parse grub menu id for kernel {kernel_version}"
        )

    saved_entry = f"{mid}>{kid}" if mid else kid

    grub_default_cfg = "/etc/default/grub.d/99-tdx-kernel.cfg"
    _write_system_file(
        grub_default_cfg,
        'GRUB_DEFAULT=saved\nGRUB_SAVEDEFAULT=true\n',
    )
    _run(
        ["sudo", "grub-editenv", "/boot/grub/grubenv", "set", f"saved_entry={saved_entry}"],
    )
    _run(["sudo", "update-grub"])


def _grub_update_cmdline(additions: list[str]):
    """Append parameters to GRUB_CMDLINE_LINUX if not already present."""
    grub_file = "/etc/default/grub"
    with open(grub_file, "r") as f:
        content = f.read()

    modified = False
    for param in additions:
        if param in content:
            print(f"  GRUB cmdline already contains '{param}', skipping")
            continue
        print(f"  Adding '{param}' to GRUB cmdline")
        content = re.sub(
            r'(GRUB_CMDLINE_LINUX="[^"]*)',
            rf'\1 {param}',
            content,
        )
        modified = True

    if modified:
        _write_system_file(grub_file, content)
        _run(["sudo", "update-grub"])
        # --no-nvram prevents grub-install from updating EFI boot order
        # (important for MAAS-managed machines that expect PXE boot)
        _run(["sudo", "grub-install", "--no-nvram"])


def _add_user_to_kvm():
    """Add the invoking (non-root) user to the kvm group."""
    user = os.environ.get("SUDO_USER") or os.environ.get("USER")
    if not user or user == "root":
        print("  Skipping kvm group (running as root with no SUDO_USER)")
        return
    print(f"  Adding {user} to kvm group")
    _run(["sudo", "usermod", "-aG", "kvm", user])


def setup_host(profile: HostProfile):
    """Execute TDX host setup using the given profile.

    Must be run as root (or via sudo). Steps:
    1. Add PPAs with apt pinning
    2. apt update
    3. Install kernel + packages
    4. Set kernel as default boot target
    5. Update GRUB cmdline
    6. Add user to kvm group
    7. Install dependencies (CLI symlinks + nvidia-gpu-tools)
    """
    print(f"\n{'=' * 60}")
    print(f"  TDX Host Setup: {profile.describe()}")
    print(f"{'=' * 60}\n")

    if os.geteuid() != 0:
        print("Error: this script must be run as root (sudo).", file=sys.stderr)
        sys.exit(1)

    # 1. PPAs
    if profile.ppas:
        print("Step 1: Adding APT PPAs...")
        _run(["apt", "update"])
        _run(["apt", "install", "--yes", "software-properties-common", "gawk"])
        for ppa in profile.ppas:
            _add_ppa(ppa, profile.codename)
    else:
        print("Step 1: No PPAs needed for this profile")

    # 2. apt update
    print("\nStep 2: Updating package index...")
    _run(["apt", "update"])

    # 3. Install kernel + packages
    print(f"\nStep 3: Installing kernel ({profile.kernel_package}) and packages...")
    all_packages = [profile.kernel_package] + profile.packages
    _run(["apt", "install", "--yes", "--allow-downgrades"] + all_packages)

    kernel_version = _get_kernel_version(profile.kernel_package)
    print(f"  Kernel version resolved: {kernel_version}")

    # linux-modules-extra may not be pulled in by the metapackage;
    # not all kernel builds ship it as a separate package (e.g. 25.10 generic).
    modules_extra = f"linux-modules-extra-{kernel_version}"
    print(f"  Ensuring {modules_extra} is installed...")
    result = subprocess.run(
        ["apt", "install", "--yes", "--allow-downgrades", modules_extra],
    )
    if result.returncode != 0:
        print(f"  {modules_extra} not available (may be built into the kernel package)")

    # 4. Set kernel as default boot
    print(f"\nStep 4: Setting kernel {kernel_version} as default boot target...")
    _grub_set_kernel(kernel_version)

    # 5. GRUB cmdline
    print("\nStep 5: Updating GRUB cmdline...")
    _grub_update_cmdline(profile.grub_cmdline_additions)

    # 6. kvm group
    print("\nStep 6: Configuring kvm group...")
    _add_user_to_kvm()

    # 7. Host dependencies (repo CLIs + nvidia-gpu-tools)
    print("\nStep 7: Installing dependencies...")
    install_dependencies()

    print(f"\n{'=' * 60}")
    print("  TDX host setup complete. Reboot to load the new kernel.")
    print(f"{'=' * 60}\n")


def _symlink_host_bin_tools() -> None:
    """Symlink host-tools/bin executables into /usr/local/bin/."""
    scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    bin_dir = os.path.join(os.path.dirname(scripts_dir), "bin")

    if not os.path.isdir(bin_dir):
        print(f"  Warning: {bin_dir} not found, skipping CLI symlinks")
        return

    for tool in os.listdir(bin_dir):
        src = os.path.join(bin_dir, tool)
        dst = f"/usr/local/bin/{tool}"
        if not os.access(src, os.X_OK):
            continue
        if os.path.exists(dst):
            if os.path.islink(dst):
                os.remove(dst)
            else:
                print(f"  Warning: {dst} exists and is not a symlink, skipping")
                continue
        print(f"  Linking {tool} -> {dst}")
        os.symlink(os.path.abspath(src), dst)


def install_dependencies() -> None:
    """Symlink host-tools/bin into /usr/local/bin and ensure nvidia-gpu-tools is available.

    When the CLI is missing, installs from the bundled wheel (venv under gpu-tools/).
    Must run as root.
    """
    if os.geteuid() != 0:
        print("Error: install_dependencies must run as root (sudo).", file=sys.stderr)
        sys.exit(1)

    print("\n=== Install dependencies ===\n")
    _symlink_host_bin_tools()
    print("\nEnsuring nvidia-gpu-tools (bundled wheel if missing)...")
    from chutes.guest.gpu.tools import ensure_gpu_tools_available

    ensure_gpu_tools_available()
    print("\nDone.\n")
