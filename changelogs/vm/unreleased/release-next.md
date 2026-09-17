### Added

- `virt-host-prereqs` now verifies the build VM's SSH keypair and `/dev/kvm` before anything is
  downloaded. Both were needed several roles later by `run-vm`, and neither failed usefully: a
  missing key surfaced as a file-lookup error while rendering cloud-init, and missing KVM did not
  fail at all — the build fell back to software emulation and took hours instead of ~30 minutes,
  with no symptom beyond appearing to hang. `ssh_public_key_path`/`ssh_private_key_path` moved from
  `run-vm` defaults to `group_vars/host.yml` so the check can see them (role defaults are
  role-scoped).

- **`docs/reproducing-measurements.md`** — how a third party independently reproduces the guest
  image measurements and compares them to the published set. Covers host prerequisites (and why
  `build-setup.yml` is the wrong tool for someone else's machine, since it rewrites apt sources and
  installs Docker), the root/`HOME=/root` requirement the plays assume, which misconfigurations the
  preflight catches versus the two it does not (a missing SSH keypair, and no KVM — which silently
  falls back to emulation rather than failing), and how to diff `/etc/tdx-rtmr3-expected-hashes` to
  name the responsible files if measurements disagree.
