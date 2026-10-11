"""The chutes_vm_config role writes config.yaml with the miner ss58 plus exactly one key.

Runs the role's real credential tasks and config.yaml template with ansible-playbook on localhost,
then loads the result with chutes-cvm's LaunchConfig: what the role writes must be what a launch
accepts. Skipped where ansible-playbook is not installed (it is not a declared dependency).

Hotkey files are the throwaway Bittensor 11 hotkey in tests/rust/fixtures.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import hotkey_fixtures as hk
import pytest
import yaml
from chutes_cvm.guest.config import LaunchConfig

REPO = Path(__file__).resolve().parents[2]
ROLES = REPO / "ansible/host/roles"
TEMPLATE = ROLES / "chutes_vm_config/templates/config.yaml.j2"
FIXTURES = REPO / "tests/rust/fixtures"


def _ansible_playbook():
    venv = Path(os.environ.get("VIRTUAL_ENV", REPO / ".venv")) / "bin/ansible-playbook"
    return str(venv) if venv.is_file() else shutil.which("ansible-playbook")


ANSIBLE_PLAYBOOK = _ansible_playbook()
pytestmark = pytest.mark.skipif(
    ANSIBLE_PLAYBOOK is None, reason="ansible-playbook not installed"
)

PLAYBOOK = """
- hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - ansible.builtin.include_role:
        name: chutes_vm_config
        tasks_from: credentials
        public: true
    - ansible.builtin.template:
        src: {template}
        dest: {dest}
        mode: "0600"
      vars:
        chutes_effective_vm_hostname: vm-test
        chutes_guest_vm_ip: 192.168.100.2
        chutes_guest_bridge_ip: 192.168.100.1/24
        chutes_guest_public_interface: eth0
"""


def _run_role(tmp_path, **extra_vars):
    """Run the credential tasks + template; return (result, rendered config or None)."""
    dest = tmp_path / "config.yaml"
    playbook = tmp_path / "playbook.yml"
    playbook.write_text(PLAYBOOK.format(template=TEMPLATE, dest=dest))
    home = tmp_path / "ansible-home"
    result = subprocess.run(
        [
            ANSIBLE_PLAYBOOK,
            "-i",
            "localhost,",
            str(playbook),
            "-e",
            json.dumps(extra_vars),
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "ANSIBLE_ROLES_PATH": str(ROLES),
            "ANSIBLE_HOME": str(home),
            "ANSIBLE_LOCAL_TEMP": str(home / "tmp"),
            "ANSIBLE_REMOTE_TEMP": str(home / "tmp"),
        },
        capture_output=True,
        text=True,
        timeout=180,
    )
    return result, (dest if dest.exists() else None)


def _hotkey_file(tmp_path, name, drop=()):
    doc = json.loads((FIXTURES / f"hotkey-bittensor11-{name}.json").read_text())
    for key in drop:
        doc.pop(key, None)
    path = tmp_path / "hotkey.json"
    path.write_text(json.dumps(doc))
    return str(path)


def _miner(config_path):
    return yaml.safe_load(config_path.read_text())["miner"]


@pytest.mark.parametrize("name", ["seedless", "full"])
def test_a_hotkey_with_a_private_key_writes_only_the_private_key(tmp_path, name):
    """Current hotkey files: privateKey wins even when secretSeed is present too."""
    result, config = _run_role(
        tmp_path, chutes_hotkey_path=_hotkey_file(tmp_path, name)
    )

    assert result.returncode == 0, result.stdout
    assert _miner(config) == {"ss58": hk.SS58, "private_key": hk.PRIVATE_KEY}
    assert LaunchConfig.from_file(str(config)).miner.private_key == hk.PRIVATE_KEY


def test_a_hotkey_with_only_a_seed_writes_the_seed(tmp_path):
    """Older hotkey files written before privateKey was stored."""
    hotkey = _hotkey_file(tmp_path, "full", drop=("privateKey",))
    result, config = _run_role(tmp_path, chutes_hotkey_path=hotkey)

    assert result.returncode == 0, result.stdout
    assert _miner(config) == {"ss58": hk.SS58, "seed": hk.SEED}
    LaunchConfig.from_file(str(config))


def test_explicit_credentials_skip_the_hotkey_file(tmp_path):
    """A seed set by hand (host_vars / Vault) keeps working as before."""
    result, config = _run_role(
        tmp_path,
        chutes_hotkey_path=str(tmp_path / "missing.json"),
        chutes_miner_ss58=hk.SS58,
        chutes_miner_seed=hk.SEED,
    )

    assert result.returncode == 0, result.stdout
    assert _miner(config) == {"ss58": hk.SS58, "seed": hk.SEED}


@pytest.mark.parametrize(
    "extra_vars",
    [
        {"drop": ("privateKey", "secretSeed")},
        {
            "chutes_miner_ss58": hk.SS58,
            "chutes_miner_private_key": hk.PRIVATE_KEY,
            "chutes_miner_seed": hk.SEED,
        },
    ],
    ids=["hotkey-without-a-key", "explicit-both-keys"],
)
def test_missing_or_ambiguous_credentials_fail_clearly(tmp_path, extra_vars):
    drop = extra_vars.pop("drop", None)
    if drop is not None:
        extra_vars["chutes_hotkey_path"] = _hotkey_file(tmp_path, "full", drop=drop)

    result, config = _run_role(tmp_path, **extra_vars)

    assert result.returncode != 0
    assert "Miner credentials are missing or ambiguous" in result.stdout
    assert config is None
