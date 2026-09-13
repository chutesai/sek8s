### Fixed
- The `chute-log-shipper` crictl boundary added in 1.4.0 did not work under enforce, so pod
  discovery failed on production images (complain-mode debug builds hid it). Three causes:
  the profile's `crictl-pods-helper cx -> crictl` transition is an LSM domain change, which
  the kernel refuses under `no_new_privs` (`NoNewPrivileges=false` now, safe because the
  profile has no `ux`/`Ux` rule and no permitted exec target is setuid); the child profile
  lacked the DAC capability the parent carries, since AppArmor mediates capability use per
  profile; and the k3s multi-call binary shells out to `xtables-nft-multi` even for
  `crictl`, which the child could not exec.
- containerd now sets the CRI socket's group itself, via a `[grpc] gid` drop-in in
  `/etc/containerd/conf.d/` (already listed in k3s's generated `imports`, so neither k3s's
  config nor its `config.toml.tmpl` is touched). Previously the socket was created
  `root:root 0660` and `k3s-ctr-socket.service` re-applied the group afterwards, which left
  a window on every containerd start and did not reliably re-run at all when k3s restarted —
  locking both `system-manager` and `chute-log-shipper` out of CRI until reboot. Ownership is
  now correct from birth, so the race is gone rather than narrowed. The `containerd` group's
  gid is pinned (10153) because the drop-in embeds it and an auto-allocated gid would drift
  the file and therefore RTMR3. `k3s-ctr-socket.{path,service}` are retained as a fallback and
  can be removed once this is proven in the field.
- `k3s-ctr-socket.service` is now `PartOf=k3s.service`. Its `.path` trigger only fires an
  inactive unit, and `RemainAfterExit=yes` kept it active forever after the first run, so a
  k3s restart left the socket `root:root` with nothing to re-run the `chgrp`.
- `sek8s.deny-sensitive-default` was the only sek8s profile without `attach_disconnected`.
  Confined binaries running inside a systemd mount namespace failed path lookup with EACCES
  on `/dev` nodes rather than being mediated.
- The apparmor-verify systemd drop-ins are now gated on `debug_build`. `Requires=` pulls a
  unit in regardless of whether it is `enabled`, so `verify-apparmor-profiles.service` ran on
  builds that were never meant to run it.
- Debug images are now reproducible: the root password hash used a random
  `password_hash()` salt, so `/etc/shadow` differed on every build and drifted RTMR3.
- The `lock-accounts` sweep now fails the build instead of the VM when a service account is
  missing from `lock_accounts_preserve`. `userdel --remove` deletes the account's home, and
  sek8s service accounts are homed at `/opt/sek8s`, so an omission silently wiped `src/` and
  the venv at build time and surfaced only as `203/EXEC` on every service at boot. A pre-sweep
  check refuses to delete any `uid >= 1000` account homed outside `/home`, naming it, and a
  post-sweep check asserts the application tree still exists.

### Security
- `99-purge-kubeconfig.sh` now purges every cluster-admin credential k3s leaves on the storage
  volume, not just `/etc/rancher/k3s/k3s.yaml`: the kubeconfig was only the entry point, while
  the client cert it embeds (`server/tls/client-admin.crt`/`.key`) and a second admin kubeconfig
  (`server/cred/admin.kubeconfig`) were left behind and are equally usable. k3s regenerates all
  of them on the next server start, and nothing that outlives init uses them, so the purge is
  invisible to runtime. Each path keeps the existing RTMR3 checkpoint semantics — still present
  after deletion means its hash is extended into RTMR3 and attestation rejects the boot.
  The CA keys are deliberately left in place: miner kubeconfigs are signed by `client-ca` via the
  CSR API and the webhooks chain to it, so rotating it breaks both across reboots. This removes a
  ready-to-use credential rather than closing the underlying exposure — `client-ca.key` remains on
  the storage volume, so offline access to that volume can still mint an admin cert.

