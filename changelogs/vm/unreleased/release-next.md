### Changed

- AppArmor shell policy is now composed rather than monolithic. A new `sek8s-shell-base`
  abstraction holds the permissions every shell profile shares, so a profile is expressed as
  "this base, plus which denies apply" instead of a hand-written allowlist — which matters under
  poweroff-on-failure orchestration, where an allowlist fails the VM for every rule its author
  forgot. `sek8s.deny-sensitive-default` is unchanged in posture: it still denies both the model
  cache and `/run/chutes`, and additionally hides the staging area described below.
- Cluster-init scripts that need a file from `/run/chutes` now run under a
  `sek8s.k3s-init.<script>` profile applied via `aa-exec`. Each still denies the model cache and
  still denies `/run/chutes` itself; the unconfined `k3s-post-start.sh` wrapper stages only that
  script's declared files into `/run/k3s-init/<script>/`, read-only, and removes them when the
  script exits. `/run/chutes` is never narrowed to make room, because AppArmor gives `deny`
  precedence over any allow and carving out exceptions would turn a fail-closed blanket into a
  denylist that silently fails open when a secret is added. Cross-script isolation comes from the
  wrapper running scripts sequentially and removing each directory before the next starts, not
  from the profiles themselves.
- Per-script access is declared in three places by design — the profile in
  `/etc/apparmor.d/sek8s.k3s-init`, the staging map in `k3s-post-start.sh`, and the check list in
  `verify-apparmor-profiles.sh`. A script absent from all three stays on the restrictive default
  profile; missing one of the three is a loud boot failure rather than a silent grant.
- `k3s-post-start.sh` no longer powers the VM off on debug builds. A failed init script previously
  powered off regardless of build type, which made the debug image unusable for diagnosing exactly
  those failures. Gated on `K3S_POST_START_DEBUG`, set by a systemd drop-in written on both builds
  and covered by the existing `/etc/systemd/system` measurement; unset means false.
- Cluster-init scripts can once again honour their own fail-closed handler. The blanket profile
  denies `capability sys_boot`, so every `FATAL: powering off VM` silently failed with
  `Failed to poweroff: Operation not permitted` and the run continued. The per-script profiles do
  not deny it.
- The RTMR3 manifest generator pins `LC_ALL=C` when sorting directory contents. `sort` is
  locale-sensitive, so collation differences between build hosts would reorder the manifest,
  changing its bytes and the initramfs that embeds it — moving RTMR2 with no content change.
