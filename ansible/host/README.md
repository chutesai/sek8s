# Ansible: bare-metal TDX host operations

Operational playbooks run from your workstation against inventory over SSH. Guest **image build** lives separately under [`../guest/`](../guest/).

## Prerequisites (controller)

- Ansible 2.14+ recommended
- `ansible-galaxy collection install -r requirements.yml` (install from this directory)
- `rsync` on the controller (`ansible.posix.synchronize`)
- `chutes-miner` and `kubectl` on the controller for `upgrade.yml`

## Layout

- `playbooks/setup.yml` — rsync `host-tools/`, packages, `setup-tdx-host`, PCCS role (automated only when both secrets are set; otherwise a clear notice or fail if misconfigured)
- `playbooks/launch.yml` — refuses launch if chutes-td QEMU is already running; then image, `config.yaml`, `quick-launch.sh`
- `playbooks/shutdown.yml` — `chutes-miner tee shutdown` from the controller, wait for `Power down` on the host (same flow as upgrade)
- `playbooks/upgrade.yml` — staged download, maintenance, drain, shutdown, cutover, relaunch
- `roles/chutes_vm_config/` — writes `config.yaml` on the host (miner credentials, `vm.hostname`, bridge network + primary NIC)

## Inventory: in-repo vs outside the repo

The **`inventory/`** directory is only there for a **checked-in example** and a default in [`ansible.cfg`](ansible.cfg) (`inventory = inventory/hosts.yml`). That way a clone runs without you having to pass `-i` the first time.

**Your own inventory** (secrets, hostnames, keys) should usually live **outside git**, e.g. copy the example and point Ansible at it:

```bash
cp inventory/hosts.yml ~/chutes/my-inventory.yml
# edit ~/chutes/my-inventory.yml (and optionally group_vars in the same tree)

cd /path/to/sek8s/ansible/host
ansible-playbook -i ~/chutes/my-inventory.yml playbooks/setup.yml
```

The **`-i` argument overrides** the `ansible.cfg` default for that invocation, so this works from any cwd as long as playbook paths are correct (either `cd ansible/host` as above, or use absolute paths to the playbooks).

You can also use a **directory** inventory (`-i ~/chutes/miner/`) with `hosts.yml` plus `group_vars/` / `host_vars/` under that tree—Ansible merges them the same way as the in-repo layout.

## Quick start

```bash
cd ansible/host
ansible-galaxy collection install -r requirements.yml

# Example: use repo sample inventory
ansible-playbook -i inventory/hosts.yml playbooks/setup.yml

ansible-playbook -i inventory/hosts.yml playbooks/launch.yml

ansible-playbook -i inventory/hosts.yml playbooks/upgrade.yml
```

Set **`chutes_miner_ss58`** and **`chutes_miner_seed`** per host (Vault or `host_vars`; never commit real values). Set **`chutes_hotkey_path`**. For automated PCCS on setup, define **both** **`pccs_api_key`** and **`pccs_password`** (Vault / `group_vars` / `-e`); defining only one fails with a remediation message; defining neither skips PCCS automation but prints why.

**`launch.yml` and `upgrade.yml`** render **`config.yaml`** on the metal host. **`vm.hostname`** defaults to **`inventory_hostname`**; set **`chutes_vm_hostname`** when the inventory key is an SSH alias. **Guest bridge addressing** defaults to a **`/24`** that does not overlap any IPv4 address on the host (see `roles/chutes_vm_config/files/pick_guest_network.py`); override with **`chutes_guest_bridge_network`** (CIDR) if needed. Set **`chutes_public_interface`** when the default-route interface is wrong or when using a manual bridge CIDR without **`ansible_default_ipv4`**.

For **`upgrade.yml`**, `chutes-miner tee … --name` uses **`tee_server_name`** defaulting to the **inventory hostname**. Set **`tee_server_name`** only when the inventory name differs from the name registered in chutes-miner.

The generated file matches the shape of [`config.tmpl.yaml`](../../host-tools/scripts/config/config.tmpl.yaml).

See [docs/specs/ansible-playbooks.md](../../docs/specs/ansible-playbooks.md) for the full contract.
