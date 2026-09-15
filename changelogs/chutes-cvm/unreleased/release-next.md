### Added

- `chutes-cvm host setup` adds the NVIDIA CUDA apt repo on Ubuntu 26.04. The Fabric Manager
  step assumed this repo was "configured already", but nothing ever added it — and it is the
  only source of a Fabric Manager matching the guest driver (Ubuntu multiverse carries neither
  the package name nor a matching version). Pinned at priority 100, below the archive, so only
  explicitly versioned requests resolve from it.

### Fixed

- `chutes-cvm host setup` could not install Fabric Manager at all: it pinned
  `595.71.05-0ubuntu0.26.04.1`, a revision no repo publishes, and asked for it under the
  Ubuntu multiverse package name (`nvidia-fabricmanager-<branch>`) rather than the CUDA repo's
  `nvidia-fabricmanager`. Now pinned to `595.71.05-1ubuntu1`, matching the guest's
  `nvidia_pkg_version`, with a unit test asserting the two stay on the same upstream version.
  Only B200/B300 hosts reach this step, so nothing on an H200 fleet surfaced it.
- Vendor signing-key fetches retry instead of aborting setup on a transient network blip.
  A single dropped connection to a vendor repo failed the whole run — which matters now that
  `upgrade-guest.yml` converges host config on every upgrade, giving a `serial: 1` fleet walk
  one chance per host to hit one.
- `_add_repo` wrote every repo's apt pin against a hardcoded `origin download.01.org`, so any
  repo other than Intel's got a pin naming the wrong origin. The pin now derives from the
  repo's own URI. It also emits no empty `Components:` line, which a flat repo needs omitted.
