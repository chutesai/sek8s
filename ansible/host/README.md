# Ansible: bare-metal TDX host operations

Operational playbooks run from your workstation against inventory over SSH. Guest **image build** lives separately under [`../guest/`](../guest/).

## Prerequisites (controller)

- Ansible 2.17+ recommended
- `chutes-miner` and `kubectl` on the controller for `upgrade.yml`

## Quick start

```bash
cd ansible/host
ansible-playbook -i ~/chutes/my-inventory.yml playbooks/setup.yml
```

## Variables by playbook

### `setup.yml` — host prep, TDX kernel, PCCS

| Variable | Scope | Required | Notes |
|---|---|---|---|
| `ansible_host` | host | **yes** | bare-metal IP or hostname |
| `ansible_user` | host | **yes** | SSH user (typically `ubuntu`) |
| `ansible_ssh_private_key_file` | host | if not default | path to SSH private key |
| `pccs_api_key` | group / Vault | **both or neither** | Intel PCCS API key; omit both to skip PCCS automation |
| `pccs_password` | group / Vault | **both or neither** | generates `UserTokenHash` / `AdminTokenHash` (SHA-512) |
| `sek8s_remote_root` | group | no | remote install root; default `/opt/sek8s` |

`setup.yml` does **not** launch the VM and does **not** need miner credentials or hotkey.

---

### `launch.yml` — render config, download image, start VM

Requires everything in `setup.yml` (SSH) plus:

| Variable | Scope | Required | Notes |
|---|---|---|---|
| `chutes_hotkey_path` | group / Vault | **yes** | controller path to Bittensor hotkey JSON; `ss58Address` and `secretSeed` are extracted automatically |
| `chutes_miner_ss58` | host / Vault | override only | skip if `chutes_hotkey_path` is set |
| `chutes_miner_seed` | host / Vault | override only | skip if `chutes_hotkey_path` is set |
| `chutes_guest_bridge_network` | host | no | e.g. `192.168.50.0/24`; auto-picked when unset |
| `chutes_public_interface` | host | no | NIC for bridge; default is default-route interface |
| `chutes_docker_hub_username` / `chutes_docker_hub_token` | group / Vault | no | optional Docker Hub block in config |

The inventory hostname is used as both `vm.hostname` in `config.yaml` and `chutes-miner --name`. These must match — do not use an SSH alias as the inventory key.

---

### `shutdown.yml` — graceful TEE guest shutdown

| Variable | Scope | Required | Notes |
|---|---|---|---|
| `chutes_hotkey_path` | group / Vault | **yes** | controller path to miner hotkey file |
| `chutes_miner_api` | group | no | miner API URL; default `http://127.0.0.1:32000` |

---

### `upgrade.yml` — stage new image, drain, cutover, relaunch

Requires everything in `launch.yml` plus:

| Variable | Scope | Required | Notes |
|---|---|---|---|
| `chutes_hotkey_path` | group / Vault | **yes** | controller path to miner hotkey file |
| `chutes_miner_api` | group | no | miner API URL; default `http://127.0.0.1:32000` |
| `chutes_kubeconfig_path` | group | no | kubeconfig written by `sync-kubeconfig`; default `~/.kube/chutes.config` |
| `upgrade_force_delete_stuck_pods` | group / `-e` | no | force-delete `Terminating` pods; default `false` |
| `chutes_validator_api` | group | no | validator API; default `https://api.chutes.ai` |

Upgrade timeouts (all in `group_vars/all.yml`, override as needed):

| Variable | Default |
|---|---|
| `upgrade_drain_timeout` | `600s` |
| `upgrade_powerdown_timeout_seconds` | `300` |
| `upgrade_health_poll_seconds` | `60` |

---

## Secrets and inventory layout

Keep secrets out of git. Copy the example inventory outside the repo:

```bash
cp inventory/hosts.yml ~/chutes/my-inventory.yml
# edit ~/chutes/my-inventory.yml
cd /path/to/sek8s/ansible/host
ansible-playbook -i ~/chutes/my-inventory.yml playbooks/setup.yml
```

Use `ansible-vault` for `pccs_api_key`, `pccs_password`, and `chutes_docker_hub_token`. A complete inventory looks like:

```yaml
# ~/chutes/my-inventory.yml
all:
  children:
    td_hosts:
      hosts:
        my-tee-host:
          # Per-host: SSH connection and any host-specific network overrides
          ansible_host: 10.0.0.10
          ansible_user: ubuntu
          ansible_ssh_private_key_file: ~/.ssh/id_ed25519
          # chutes_guest_bridge_network: ""  # e.g. 192.168.50.0/24; auto-picked when unset
          # chutes_public_interface: ""      # NIC for bridge; default is default-route interface

      vars:
        # Group-wide: same for all hosts managed by this operator

        # Miner hotkey (launch / shutdown / upgrade)
        # ss58Address and secretSeed are extracted automatically.
        chutes_hotkey_path: ~/.bittensor/wallets/mywallet/hotkeys/myhotkey

        # PCCS (setup only) — set BOTH or omit BOTH
        # pccs_api_key: !vault ...
        # pccs_password: !vault ...

        # Optional group overrides
        # chutes_miner_api: ""             # miner API URL; default http://127.0.0.1:32000
        # chutes_docker_hub_username: ""
        # chutes_docker_hub_token: !vault ...
```

`ss58Address` and `secretSeed` are read from the hotkey JSON on the controller at play time — no need to copy them into inventory. To override either value, set `chutes_miner_ss58` / `chutes_miner_seed` explicitly and the hotkey parse is skipped.

The inventory hostname (`my-tee-host` above) is used as both `chutes-miner --name` and `vm.hostname` in `config.yaml`. These must match the TEE server name registered in chutes-miner — do not use an SSH alias as the inventory key.

The generated `config.yaml` matches the shape of [`config.tmpl.yaml`](../../host-tools/scripts/config/config.tmpl.yaml).

---

## OS support

| Ubuntu | Status | TDX kernel |
|---|---|---|
| **26.04** | current target | native `linux-image-generic` |
| 25.10 | **EOL July 2026** — migrate to 26.04 | kobuk-team PPA `linux-image-intel` |
| 25.04 | **EOL Jan 2026** — migrate to 26.04 | kobuk-team PPA `linux-image-intel` |

See [docs/specs/ansible-playbooks.md](../../docs/specs/ansible-playbooks.md) for the full contract.
