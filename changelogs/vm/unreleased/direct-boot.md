### Added

- **Chute log shipper (guest side, Phase 1).** New `chute-log-shipper` systemd service in the
  attested guest image that closes the gap where a chute crashing before instance registration
  left its logs unreachable. It discovers chute pods locally via the CRI socket
  (`k3s crictl pods -o json`, through a restricted `crictl-pods-helper` wrapper), reads their logs
  off `/var/log/pods/chutes_*/…`, and streams them to the validator over the per-boot registry mTLS
  leaf. Runs as a dedicated non-root uid, confined by the `sek8s.chute-log-shipper` AppArmor profile
  (only the chute log paths, the CRI socket, the registry-tls leaf, the cursor dir, and egress).
  - New Python package `sek8s.log_shipper` (`config`, `crictl`, `cursor`, `shipper`, `agent`) with
    a `chute-log-shipper` console entry; no new dependencies (reuses `aiohttp` + the `run_command`
    shell pattern, no k8s API access).
  - New Ansible role `chute-log-shipper` (registered in `chutes-miner-vm.yml`) plus the AppArmor
    profile delivered via `apparmor-hardening`. The registry mTLS leaf is untouched at mint time
    (RTMR2 stable); a boot-time path unit re-groups it for the service's uid.
  - Wire contract: `POST https://cvm.chutes.ai/instances/launch_config/{config_id}/logs` with a
    log-lines-only body (`{"logs": [{ts, stream, log}]}`) — identity is derived validator-side from
    the mTLS leaf + path + proxy, nothing is self-asserted. `204` = stop capture; other 2xx = keep
    sending; dedupe is `(config_id, ts)`, so no `seq` is sent (deviation from the original spec,
    which listed `seq`).
