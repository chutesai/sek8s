"""Where chutes-cvm finds its bundled scripts and its external runtime data.

Two kinds of thing:

* **Bundled** (small, ship with the package): the VM-management shell scripts, the
  config JSON schema, and the vfio udev rules under ``chutes_cvm/scripts/``. Resolved
  package-relative, so they travel with a ``pip install`` — no checkout needed.
* **External** (large, not bundled): the guest firmware (OVMF) and the gpu-tools wheel.
  These live in the checkout; resolved checkout-relative for an editable install, with
  an env override for a non-editable / PyPI install.
"""

import os
from pathlib import Path

# chutes_cvm/ — bundled data lives under here.
PACKAGE_DIR = Path(__file__).resolve().parent
# VM-management shell scripts + config schema + udev rules, shipped with the package.
SCRIPTS_DIR = PACKAGE_DIR / "scripts"

# The checkout root, for editable installs (chutes_cvm -> chutes-cvm -> src -> repo).
_REPO_ROOT = PACKAGE_DIR.parents[2]


def firmware_dir() -> Path:
    """The guest firmware (OVMF) directory. Env override for non-editable installs."""
    return Path(os.environ.get("CHUTES_CVM_FIRMWARE_DIR") or (_REPO_ROOT / "firmware"))


def gpu_tools_dir() -> Path:
    """The bundled nvidia-gpu-tools wheel directory (stays in host-tools; dev/build owns it)."""
    return Path(
        os.environ.get("CHUTES_CVM_GPU_TOOLS_DIR")
        or (_REPO_ROOT / "host-tools" / "scripts" / "gpu-tools")
    )


def default_config_path() -> str:
    """The miner's launch config.yaml. Operator data (not bundled): the deployed location
    under the checkout's host-tools/scripts/, or the CHUTES_CVM_CONFIG override. Callers
    (ansible, quick-launch) usually pass an explicit path instead."""
    return os.environ.get("CHUTES_CVM_CONFIG") or str(
        _REPO_ROOT / "host-tools" / "scripts" / "config.yaml"
    )
