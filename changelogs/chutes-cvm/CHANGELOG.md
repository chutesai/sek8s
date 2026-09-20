# chutes-cvm Changelog

The `chutes-cvm` CLI + toolkit (`src/chutes-cvm/`) — an independently installable host CLI
(`pip`/`install.sh`). Versioned with SemVer via `src/chutes-cvm/VERSION`. Run
`make promote-changelogs` to aggregate fragments into the current version section.
## [0.2.0] - 2026-09-20

### Changed
- `discover-profile.sh` emits per-device lists — `gpus`, `nvswitches`, `ib_devices` — each object
  carrying its own BDF, vendor/device/class, NUMA node and BAR layout. Previously the document
  described a platform through parallel arrays (`gpu.bdfs`, `gpu.numa_nodes`, `nic.ib_devices`,
  …) whose entries had to be zipped back together by index at every reader. BAR sizes come from
  world-readable `/sys/bus/pci/devices/*/resource`, so the capture still runs unprivileged and
  works with the GPUs already bound to `vfio-pci`. The parallel-array blocks they replace (`gpu`,
  `nic`, `nvswitch`) are gone, along with the array builders that fed them -- they had nowhere to
  put a BAR layout, which is why the device lists exist. `host` and `pci_topology` stay as operator
  diagnostics; neither is submitted.
- New `chutes_cvm/guest/devices.py` models those lists (`PciBar`, `GpuDevice`, `NvSwitchDevice`,
  `IbDevice`). Every key is required — defaulting a missing one would turn a malformed document
  into a device with no identity — and devices sort by BDF at construction, so ordering is an
  invariant rather than something each caller re-establishes.
- New `chutes_cvm/guest/host_profile.py` is the single reader of a capture. `HostProfile` holds
  the raw document and resolves everything derived from it — guest CPU/RAM, `-smp` topology,
  NUMA vectors, which endpoints the profile actually passes through, and the QEMU command
  itself. Launch, offline measurement generation and profile submission all build from it, so
  the three paths can no longer drift on how they read the same host.
- Renamed the `launch_determinism` block to `qemu` and dropped its `numa_node_count`,
  `numa_topology_eligible`, `cpu_args` and `host_cpu_topology` members. The name fit when the
  block also carried the guest `-smp` string; that left when reserved CPUs became per-profile.
  The four dropped members either restated `numa`/`cpu` or were re-derived by every reader, and
  nothing read them. What remains is the QEMU build identity.
- The measurement adapter builds each GPU's `pci-bar-stub` from the captured device rather than a
  hardcoded per-profile table. OVMF sizes the guest's 64-bit MMIO aperture from the BARs it
  enumerates, so the host is the authoritative source by construction; a GPU model with no table
  entry previously could not be measured at all. The per-profile `passthrough` tables are gone
  entirely, along with the fallback that read them: BAR2 is resizable, so two machines of one
  model can differ and only the capture knows which. A device that captured no BARs now fails
  loudly rather than being measured against a shipped constant.
- Renamed `measurement/platform_tables.py` to `measurement/image_config.py` and `host/profiles.py`
  to `host/recipes.py`, each in its own commit. `platform_tables` stopped being tables when the
  data moved to the host profile; what the module produces is the tdx-measure image config.
- Removed `guest/command.py`, `measurement/topology_spec.py` and `guest/gpu/topology.py`, and cut
  `guest/detection.py` to the detectors still called. Each held a second way to describe a host
  that `HostProfile` now covers.
- `HostProfile.to_api_profile()` is what `submit-profile` sends: the RTMR0 determinants and
  nothing else. Stored == hashed == required, one set -- an unhashed field stored beside a hashed
  one re-splits the class at the byte level, so two hosts that measure identically differ in the
  stored row and the API's first-write-wins silently drops one. Host RAM is the worked example:
  2007 GB and 2011 GB are one H200 class, and keying the host total split them. Device host
  addresses go the same way: the measured command swaps every endpoint for a `pci-bar-stub`, so a
  BDF never reaches RTMR0 and only says which slot a card sits in. A stored profile reads back
  into the same `HostProfile`, and generating from one produces a byte-identical RTMR0 to
  generating from the full capture. Profiles shrink from 28-46 KB to 0.9-2.3 KB.
- Guest RAM is resolved in exactly one place -- `HostProfile.from_host()`, when the host is read --
  and carried from there through submission, storage and generation. `guest_mem_gb` is a plain
  read that refuses a document without it rather than re-deriving. The sizing rules are
  per-`GpuProfile`, so a later release deriving a different answer would measure one guest and
  file it under a key computed from another; storage drops the host total anyway, which makes
  re-deriving impossible as well as wrong. A profile predating the current capture now fails
  loudly instead of being measured from a value this release invented.
- The capture's `cpu` block is `count` / `vendor` / `processor_id`, matching `HostCpu`. It said
  `total` / `cpu_vendor` / `cpu_processor_id` while the class said the other thing, so a
  round-tripped profile came back `count=0, processor_id=None` -- a guest with no CPUs, measured
  without complaint. One spelling end to end.
- `submit-profile` sends `HostProfile.to_api_json()` rather than the whole capture. `bdf` stays
  mandatory on a capture -- it binds the device and orders the list -- and is simply not sent;
  `from_api_profile()` supplies positional stand-ins when a stored profile is read back to
  generate its measurement, at the boundary where they are known to be meaningless. `--target-os`
  now rewrites only the QEMU version, since the OS release is no longer submitted.
- A launch states the pcie.0 slots of its six emulated devices (boot disk, NIC, the three volumes,
  vsock) instead of letting QEMU auto-assign them. The layout is unchanged — QEMU picked the same
  slots — but it is now written in the command, which is what lets offline generation reproduce it
  by reading the command rather than re-deriving the rule. `PcieRootPinning` owns the rule for both
  paths and every builder now requires the caller's pinning object, so the slots cannot be decided
  twice. Command assembly stays pure: the same inputs give the same command, and the measurement
  adapter substitutes backing-free fillers at the addresses already there.
- A launch took `-cpu` from the `GUEST_CPU_ARGS` constant while measurement generation resolved it
  from the profile's QEMU version. They agreed only because the version table holds one entry
  pointing at that same constant, so the first host on a second QEMU version would have booted with
  one `-cpu` having been measured with another — an attestation failure with nothing in the command
  to show why. The launch now reads `host.cpu_args`, the same resolution generation uses.
- A launch reads the host profile unconditionally. It previously read one only under
  `--pass-gpus`, so `--no-gpus` booted a hardcoded 100G/32-vcpu/single-socket guest that no
  profile described and no measurement was ever generated for — a second, unattestable guest
  shape reachable by a flag, expressed as `host is not None` guards scattered through the
  launcher. `--no-gpus` now means what it says: the same guest this host is measured for,
  without binding its GPUs.
- `cpu_args` is a lookup on `HostProfile` (`CPU_ARGS_BY_QEMU`) rather than a module-level table
  and helper function in `qemu.py`. One QEMU version maps to one `-cpu`; the host profile knows
  the version, so it is where the answer belongs.

### Fixed
- Offline generation built NVSwitch and InfiniBand root ports for devices the launcher does not
  attach — on a B300, 14 `rp_ib` ports that never exist in a real boot — because it took the raw
  NUMA vectors while the launch path gated on the GPU profile's `passthrough` entries. Both paths
  now gate identically, so a generated RTMR0 can match the boot it describes.
- Every flat-topology guest measured one pcie.0 slot high. Offline generation hardcoded the
  guest-NUMA slot run (0x2-0x7) for the emulated devices, but a flat guest pins nothing and QEMU
  fills from 0x1, so each DSDT device node shifted, changing the ACPI digest and so RTMR0. No flat
  class ever reproduced its own boot. Confirmed against live CCELs captured from both paths on one
  host: NUMA `_ADR` slots [2,3,4,5,6,7,24,25,31], flat [1,2,3,4,5,6,8,9,31]. A forced-flat boot's
  real RTMR0 now regenerates byte-for-byte offline.
- Generating a flat topology also needs tdx-measure's `fix/pxb-roots`: the fork emitted an
  `extra-pci-roots` event unconditionally, adding a 15th RTMR0 event to guests that declare no PXB
  bridges. Both bugs had to be fixed for a flat class to measure correctly, which is why only
  guest-NUMA hosts ever attested.

### Removed
- The per-GPU `opt/ovmf/X-PciMmio64Mb<N>` fw_cfg hint and its `bar_size_mb` plumbing. The
  firmware reads a single unsuffixed key, so the suffixed ones were never matched — `strings` on
  the shipped OVMF confirms it. OVMF auto-sizes the 64-bit window from the passed-through BARs.
  Every baselined RTMR0 is byte-identical with the code removed.

## [0.1.0] - 2026-09-17

### Added
- **`chutes-cvm measurements`** — TDX measurement generation is now a first-class command
  group (`generate` / `list`), forwarding to `chutes_cvm.measurement`. The guest build
  calls the CLI instead of per-register shell scripts + ansible roles:
  - **The API is the source of truth for known host classes.** `generate` reads the published host
    profiles (`GET /servers/tdx/host_profiles` — public, unauthenticated; `--api-base`, default
    `https://api.chutes.ai`) and produces ONE entry per class: it derives the topology from each
    stored discover-profile document (mirroring the live `host_topology_fingerprint`), runs the fork
    offline, and **carries the API's 64-hex `fingerprint` through onto the entry** (never recomputed).
    The reconciler joins published measurements to submitted host profiles on that fingerprint, so it
    is required — an entry without one is unmatchable ("pending" forever even though it launches). The
    in-repo baseline registry (`known_topologies`, `GpuProfile.baselined_measurements`) is removed:
    adding hardware is `chutes-cvm host submit-profile`, not a code change here. By default only
    *measured* classes are processed (what a third party can verify); **`--include-pending`** also
    processes classes awaiting generation (the generator's queue), which the release build passes to
    turn newly submitted profiles into published measurements.
  - **`generate`** — with no `--register`, computes EVERY register in one POST-LUKS call — mrtd +
    RTMR0 (all API host classes) + RTMR1/RTMR2 (the image's staged direct-boot artifacts) + RTMR3
    (mounting the root, unlocking it with `LUKS_PASSPHRASE`) — and writes the version's single
    `measurements.yaml`. This is what the miner-VM build runs; there is no separate pre-LUKS RTMR3
    step.
  - **`generate --register rtmr0`** — just the version-level MRTD + per-topology RTMR0 via the
    tdx-measure fork (offline, any x86-64 Linux — no TDX/GPU) as a JSON block. A standalone partial
    (a full `generate` computes RTMR0 inline).
  - **`generate --register rtmr3`** — just the version-level RTMR3 (SHA-384 chain over the image's
    `/etc/tdx-measure.conf` files), mounting the root read-only. `LUKS_PASSPHRASE` unlocks an
    encrypted root; always recomputes **fresh** (the real value, no cached/reused fallback). Used by
    the partner GPU-VM build (`tee-gpu-vm.yml`), which has no aggregation.
  - **`list`** — prints the API's known host classes (fingerprint + GPU summary).
- **`src/chutes-cvm/install.sh` — the single source of truth for install** (replaces
  `host-tools/scripts/provision/setup-chutes-cvm.sh`). One script owns both fetch and install, and
  picks its mode: run from a checkout (ansible / build / dev) → editable install from that checkout
  (a `git pull` updates the code, no reinstall); `curl -sSL …/src/chutes-cvm/install.sh | bash` →
  sparse shallow-fetch (`host-tools/` + `firmware/` + `src/chutes-cvm/`) into a **temporary** dir,
  non-editable install (CLI + bundled nvidia-gpu-tools into a persistent venv, firmware copied next
  to it), then delete the temp checkout. The sparse path-list and the install steps live here
  exactly once; the `host_tools` ansible role and the guest build both invoke it. A standalone
  install is fully launch-capable — no manual `git clone`, no lingering source, no PyPI, no R2.
  `chutes-cvm host setup` no longer installs/verifies the CLI or gpu-tools (install.sh installs
  them; the launch path verifies gpu-tools where it matters); its `--install-tools-only` flag and
  the `install_dependencies` step are removed.
- **`make bundle-gpu-tools`** — discoverable maintainer target that rebuilds the vendored
  nvidia-gpu-tools wheel into the package (recipe at `src/chutes-cvm/tools/gpu-tools/`).
- **`chutes-cvm image` / `chutes-cvm config`** — the base-image tool (`image download` /
  `image verify` / `image manifest`) and the config validator (`config init` / `config verify`) are
  noun groups, so every caller routes through the one console script.
- **`chutes-cvm guest` — the TDX VM runtime lifecycle, grouped under one noun** (mirroring `host`):
  `guest launch` / `stop` / `down`. The operator surface is all nouns; the low-level QEMU boot
  primitive (`chutes_cvm.guest.__main__`) is not a CLI command — `guest launch` reaches it via a
  Python import. GPU/PCI hardware ops (`reset-gpus`, `vfio-wedged`) live under `host`, since they act
  on host hardware with or without a running guest.
- **`chutes-cvm guest launch`** — end-to-end VM launch orchestrator (`chutes_cvm.guest.launch`): a
  Python decision layer that resolves config with precedence (CLI > YAML > defaults), validates, runs
  the host gates (TDX active, NUMA, duplicate-VM guard), then runs each privileged step — the
  bundled bash helpers for the tool-sequence ones (volumes, config volume, bridge), and in-process
  `sudo` file ops for the per-VM image copy — and boots via the QEMU boot
  primitive. This is the one command a miner uses to bring a VM up. Per the AGENT.md bash-vs-Python
  rule, Python owns the decisions and bash still owns the root system mutations (cryptsetup/mkfs/nbd,
  ip/iptables).
- **Launch gates on a measurement for THIS image, not just the host class.** Before any
  GPU/volume/boot work, `guest launch` runs the same control-plane check as `host verify`: it reads
  the base image's `(version, rc)` from its manifest, captures + signs the host profile, and asks
  `POST /servers/tdx/preflight` whether a *published measurement for that exact `(version, rc)`*
  covers this host class. A stored host profile is no longer treated as launchable — a class can be
  registered (or measured for a different version) yet have no measurement for the image you are
  about to boot. If it is not launchable it refuses early instead of booting a VM that would only
  fail attestation, pointing you at `host submit-profile`; fails closed if the API is unreachable;
  `--force` overrides (with a warning). Only **benchmark** VMs skip it (dummy creds, not attested);
  **debug (RC)** images are no longer special-cased — their `rc:true` measurement must be published
  just like a production image's, which the `(version, rc)` join checks directly.
- **`chutes-cvm image download` / `config init` / `guest stop` / `guest down`** — the launch
  orchestrator's modes that used to be flags are now first-class commands: `image download [--debug]`
  fetches + verifies a base image set, `config init` scaffolds a `config.yaml`, `guest stop` shuts
  down only the VM (leaving the bridge up), and `guest down` tears the whole environment down (VM +
  bridge + benchmark-netlog).
- **`chutes-cvm guest stop` and `guest down` shut the guest down gracefully by default.** Both POST
  a hotkey-signed request to the guest system-manager API (`http://<vm_ip>:8080/status/system/shutdown`,
  the same endpoint the chutes-miner control plane uses) so the VM powers off cleanly — a miner can
  shut down gracefully with only their config.yaml, no chutes-miner CLI needed. `stop` does just the
  guest shutdown (bridge + volumes left in place); `down` additionally tears down the host-side
  bridge + benchmark-netlog — the extra dependency cleanup is the only difference. `--force` skips
  the API and force-kills QEMU on either command; a graceful attempt that can't reach the API stops
  and points the operator at `--force`.
- `chutes-cvm host submit-profile --target-os <release>` — register the host class this machine
  becomes after an OS upgrade (target release, its QEMU, its `-cpu` args), mirroring
  `host verify --target-os`. Unsupported releases are rejected before anything is signed or sent.
- `chutes-cvm host setup` adds the NVIDIA CUDA apt repo on Ubuntu 26.04. The Fabric Manager
  step assumed this repo was "configured already", but nothing ever added it — and it is the
  only source of a Fabric Manager matching the guest driver (Ubuntu multiverse carries neither
  the package name nor a matching version). Pinned at priority 100, below the archive, so only
  explicitly versioned requests resolve from it.
- **`chutes-cvm update`** — updates the CLI in place by re-running `install.sh`, so there is no
  second copy of the install logic to drift from how the host was set up. It resolves the install
  mode the same way `version` does: an editable install (the CLI resolves from a checkout) gets a
  `git pull --ff-only` followed by a re-install from that checkout, while a non-editable install
  (the `curl | bash` route discards its source, so there is nothing to pull) fetches the installer
  at `--ref` and runs it. `--ref` defaults to `main`, matching `install.sh`, with a tag as the
  override. Requires root — it writes the venv and the `/usr/local/bin` shim — and says so rather
  than failing with a permission traceback.
  - Deliberately does not check the CLI against the installed guest image: nothing declares which
    CLI versions pair with which image versions, so such a check could only compare two numbers
    with no rule relating them.
  - Hands off with `execv` rather than a subprocess, so the interpreter is replaced instead of
    reinstalling the package it is importing from. Everything printed after the handoff is
    `install.sh`'s output.

### Changed
- `B300` guest RAM is sized against the host instead of pinned to aggregate VRAM. The
  host-tools profile always requested `vram_gb * gpus` (2304G for 8), so a ~2 TB sled
  aborted at launch; it is now capped at what the host can back. Hosts with enough RAM
  are unaffected, so no in-service B300 is re-baselined.
- **The guest build's measurement phase is entirely CLI-owned — no measurement roles.** The
  `compute-rtmr0` / `compute-rtmr1-2` / `compute-rtmr3` / `aggregate-measurements` roles and their
  shell scripts are removed; the miner-VM build calls `chutes-cvm measurements generate` once,
  POST-luks, for every register. The CLI unlocks the encrypted root with `LUKS_PASSPHRASE` to read
  RTMR3's userspace files (always fresh — no cached-value reuse), so there is no longer a separate
  pre-LUKS RTMR3 stage. `libguestfs-tools` (guestmount) is now a build-host prereq
  (`build-setup.yml`); `tee-gpu-vm.yml` calls `measurements generate --register rtmr3` inline. The
  generator's firmware paths resolve via `chutes_cvm.paths`.
- **Host provisioning installs the package** — `host_tools` stages and runs the package's
  `install.sh`, which fetches + `pip install -e`'s the package into a venv and puts the
  `chutes-cvm` console script on PATH (with its deps: pyyaml/pydantic-settings/substrate-interface). Host
  ansible calls `chutes-cvm <command>` instead of `python3 -m chutes.guest.*`, so dependency-bearing
  commands (`config`, and the API-backed `host verify`) run with their deps available. The sparse
  checkout now includes `src/chutes-cvm/`. Guest image build keeps `PYTHONPATH` (stdlib commands
  only). Set `CHUTES_CVM_PYPI=1` to install from PyPI instead of the checkout.
- **Host lifecycle + attestation live under one `chutes-cvm host` group.** `host setup` / `verify`
  / `submit-profile` / `tune` / `restore` replace the former top-level `setup-host` / `verify-host`
  / `tune-host` / `restore-host`; the standalone `discover-profile` command is dropped (its capture
  is done inline by the verify/submit flow — `discover-profile.sh` stays as the bundled helper).
  `host verify` is API-backed: Gate A (host runs its OS release's QEMU) stays local; Gate B reads the
  base image's `(version, rc)` from its manifest, captures the host's platform metadata
  (discover-profile.sh), signs it with the miner hotkey (sr25519), and asks
  `POST /servers/tdx/preflight` whether a published measurement for that `(version, rc)` covers this
  host class — the control plane owns the fingerprint and returns a single `launchable` verdict,
  replacing the in-repo `known_topologies` set. `--target-os` checks against a target OS's QEMU, and
  `--base-image` picks which image set to check (pre-upgrade). `host submit-profile` registers an
  unmeasured host class (`POST /servers/tdx/host_profiles`, a distinct operation from the preflight
  check), run when the check reports the class is not yet launchable. Fails closed (BLOCKED) when it
  can't get a verdict. Adds `substrate-interface` to the chutes-cvm package for the signature.
- **`detect_profile` no longer gates on a local baselined set.** It resolves the GPU profile and the
  live fingerprint (which still drive the launch `-smp`/`-m`); acceptance is the control plane's call.
- **The launch orchestrator is Python, not a bash script.** The former `quick-launch.sh` is ported
  to `chutes_cvm.guest.launch` (`chutes-cvm guest launch`): Python owns arg/config precedence,
  validation, the host gates and the duplicate-VM guard, and calls the bundled bash helpers for the
  privileged volume/network steps then boots via the QEMU boot primitive
  (`chutes_cvm.guest.__main__`, reached by import — not a CLI command). Its old `--download` /
  `--template` / `--clean` early-exit modes are the first-class `image download` / `config init` /
  `guest down` commands above. The `config.tmpl.yaml` template moved into the package (so
  `chutes-cvm config init` can emit it); the config `.example.yaml` files stay in
  `host-tools/scripts/config/`. A deprecated `host-tools/scripts/quick-launch.sh` shim remains
  (forwards to `chutes-cvm guest launch`) so existing
  miner automation that invokes the script by path keeps working across the upgrade; when run from
  a checkout without the CLI installed, it bootstraps it via the checkout's `install.sh` (editable)
  so `git pull` + the wrapper gets a host going with no separate install step.
- **Launch config is one pydantic-settings model (`LaunchConfig`).** It is the single source of
  fields, defaults, validation, and precedence — **CLI > env (`CHUTES_CVM_*`, nested with `__`) >
  config.yaml > defaults** (nested sections deep-merge across sources) — replacing the hand-rolled
  defaults/flag maps and the KEY=value shell bridge. The model uses per-area nested sections (`vm`,
  `network`, `volumes`, …) that **mirror the existing `config.yaml` structure, so miners' configs
  load natively with no migration**. The same model generates a starter file: `chutes-cvm config init`
  emits a schema-derived, commented config, and `chutes-cvm config verify <file>` validates against the
  model. This drops `jsonschema` and the `config-schema*.json` / `config.tmpl.yaml` files for
  `pydantic-settings`; `chutes-cvm guest down` reads the network values in Python and passes them to
  `teardown.sh` (no more `chutes-cvm config` eval round-trip).
- **`chutes-cvm host setup` is now the complete per-host configuration.** It folds in what were
  three ansible roles so that running the CLI fully provisions a host (launch-ready modulo the CLI
  install itself, PCCS secrets, and a reboot): the `ntp` role becomes `_setup_ntp()` (chrony with
  `makestep` to step a skewed BMC RTC before any VM inherits the host clock), the `chutes_dirs` role
  becomes `_ensure_chutes_dirs()` (`/var/lib/chutes/base-images` + `vm-images`), and the host
  operational deps from `host_prerequisites` (chrony, aria2, xfsprogs, python3-yaml) move into a new
  version-independent `HostProfile.base_packages` installed alongside the kernel + TDX stack.
- **The `setup.yml` host-setup playbook is now thin orchestration.** It runs only `host_tools`
  (bootstraps the CLI), `tdx_bootstrap` (`chutes-cvm host setup` + reboot + TDX-init verify), and
  `pccs_configure` (vault-held PCCS secrets) — the boundary is: the CLI owns per-host config,
  ansible owns bootstrap, secrets, and fleet reboot/verify. The `host_tools` role now self-ensures
  its own install prerequisites (python3-venv/pip **+ git** for the sparse fetch), so no separate
  pre-CLI `host_prerequisites` step is needed in setup.
- **`chutes-cvm host verify` no longer requires a downloaded base image.** Verification asks a
  version-free question — *is this host class known, and which published images cover it?* — via the
  new `POST /servers/tdx/host_profiles/status`, replacing the per-version
  `POST /servers/tdx/preflight` call. Previously a host with no image set reported
  `BLOCKED (image): manifest.json missing`, which made the gate unreachable on exactly the hosts
  that need it: a brand-new box is verified (and `--submit`-registered for measurement) *before* it
  downloads anything. The two gates are now cleanly split — `host verify` answers "can this host run
  anything, and what", `guest launch` keeps its unchanged per-version preflight against the
  `(version, rc)` it actually holds.
  - READY now lists the images the class can launch, so an operator sees what to download.
  - A class that is registered but awaiting measurement generation is reported as such and is no
    longer prompted to re-submit — re-submitting does not advance the queue.
  - If a base image *is* downloaded, its `(version, rc)` is checked against the covered set as a
    **note**: an uncovered image is flagged (exit 2) but never invalidates the host-class verdict.
    `--base-image` now selects the image for that note only.
- **`chutes-cvm image manifest` now writes `manifest.json` next to the qcow2 by default**
  (previously `<base>.manifest.json`). `manifest.json` is the only name the readers —
  `image verify`, `image download`, and `guest launch` — look for, so a freshly generated
  set is directly consumable and copyable as a whole directory. Pass `-o` for the old name.
- Offline RTMR3 prediction now runs the same `tdx-measure` script the guest runs, instead of
  reimplementing the walk in Python. The script is bundled with the package, so a `pip install
  chutes-cvm` can still predict a measurement with no checkout, and the guest image is built by
  copying that same file in. Previously this module decided independently which files to measure,
  in what order, and how to hash them, and had drifted from the guest in ways that would have made
  a predicted measurement disagree with the one a VM actually produces. Only the chain fold stays
  in Python, because at boot the hardware does the folding.
- **RTMR3 is now a single hardware extend over a digest of the measured file list**, rather
  than one extend per file: `rtmr3 = SHA384(0^48 || SHA384(hash-list))`, where the hash list is
  `tdx-measure hash` output verbatim. 41k per-file TDCALLs cost ~167s of every boot and bind
  nothing a single extend over the ordered list does not. Hashing the list text also binds the
  measured paths, which the per-file content chain did not. **Published RTMR3 measurements must
  be regenerated.**
- `tdx-measure` batches its hashing through `xargs` instead of forking `sha384sum` per file,
  which was the entire cost of the hashing phase (~6ms per file, 255s for 41k files on a real
  guest; 30x faster on a 6k-file bench here, output byte-identical). `sha384sum -z` disables
  GNU's filename escaping — the hazard that forced the per-file stdin form, where a backslash
  in a name silently shifted the hash field — and xargs preserves input order, so digests pair
  positionally with the sorted path list and the echoed names are ignored entirely.

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
- `install.sh` no longer describes the repository as private, which implied the fetch needs git
  credentials it does not need. `README.md` no longer states the package is published to PyPI, and
  explains why it is deliberately not; the `pyproject.toml` `include` comment no longer justifies
  itself by that path (the `include` is unchanged — the non-editable install needs those files in
  the built wheel).

### Removed
- **The `ntp` and `chutes_dirs` ansible roles** — folded into `chutes-cvm host setup` (above). The
  `host_prerequisites` role stays (still used by the launch / remediate / build-setup playbooks) but
  is no longer part of `setup.yml`.
- Removed the lab-validated host topology matrix (`chutes_cvm/host/support_matrix.py`) and the
  `chutes-cvm host setup --topology-matrix` flag that printed it. The matrix was a hardcoded
  set of (Ubuntu, GPU SKU, GPU count) triples maintained by hand and consulted by nothing —
  its lookup helper had no callers, so it documented support rather than enforcing it, and it
  drifted from reality (B300 was listed by the formatter but absent from the data). Host
  support is now determined by probing the actual host with the chutes-cvm CLI, and the set of
  supported profiles is served by the API, so the static table was a second source of truth
  with no way to stay correct. The validated-topology table in `host-tools/README.md` remains
  as operator documentation.
- The PyPI install path (`CHUTES_CVM_PYPI` / `CHUTES_CVM_VERSION`). It was left over from the
  original consolidation design and could never have worked: the package bundles the NVIDIA GPU
  admin tools wheel, which is not installable as a dependency, and it is paired with firmware that
  ships outside the package entirely. The firmware copy was in fact skipped in that mode — and so
  was the warning about it — so a PyPI install would have succeeded and then failed at VM launch
  with nothing pointing at the cause. `install.sh` now has the two modes it actually supports,
  repo-present (editable) and bootstrap (non-editable), and the now-unused `MODE` variable is gone.

