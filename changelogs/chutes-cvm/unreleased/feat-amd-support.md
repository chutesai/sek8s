### Added

- `firmware/OVMF.amdsev.fd` is pinned in the repo alongside `OVMF.inteltdx.fd`, with
  `firmware/PROVENANCE.md` recording its origin and digest, and `build-firmware.sh
  --amd-sev` to rebuild it from edk2. The SEV-SNP launch measurement is a hash of
  these exact bytes, so resolving firmware from `/usr/share/ovmf` would let a distro
  package update silently invalidate every published measurement.
- `OVMF.amdsev.fd` is built from source (edk2-stable202605, `AmdSevX64.dsc`) in a
  digest-pinned `ubuntu:26.04` image, reproducibly, with two deviations from upstream:
  `PcdUse1GPageTable|TRUE`, without which the firmware clamps guests to 40 address bits
  and hangs silently whenever the 64-bit PCI window lands above 1 TiB (any GPU passthrough
  with ~768 GB of guest RAM), and an empty embedded-GRUB placeholder, since the GRUB serves
  a launch-secret flow we do not use and cannot be built on Ubuntu (`linuxefi.mod`,
  `sevsecret.mod`). The firmware now sizes the MMIO window itself, so no
  `X-PciMmio64Mb` hint is needed. Verified with 8x RTX PRO 6000 passed through under
  SEV-SNP. Replaces the vendored Ubuntu `ovmf-amdsev` binary; changes the SNP launch
  digest (none published yet).
- `chutes_cvm.guest.tee`: host-side TEE abstraction covering the QEMU arguments that
  actually differ between Intel TDX and AMD SEV-SNP — the confidential-guest object,
  the memory backend type, the machine flags, the SMBIOS product string and the
  firmware filename. Everything else in `QemuCommand` — drives, netdevs, PCIe
  topology, direct boot — is identical across both platforms and untouched.
- `TeeProvider.verify_environment()` raises unless *this* provider's platform is
  switched on, reading the kvm module parameter the provider owns (`kvm_param`) and
  reporting the remedy the provider owns (`enablement_hint`). Which platform a host
  class runs is derived from its CPU vendor; whether the machine in front of you has
  it enabled is a BIOS setting and a separate question. Keeping the two apart is what
  produces "SEV-SNP is not enabled in BIOS — check SMEE, ASID space limit, TSME"
  instead of an opaque failure inside QEMU.
- `HostProfile.verify_environment()` asks the profile whether this host's environment can run
  the guest it describes, delegating to the provider method of the same name. Named for the
  environment rather than the launch: a profile describes a machine and knows nothing about
  booting a guest. `chutes-cvm guest launch` Step 0 takes THE reading of the host and asks it,
  rather than probing the platform itself.
- `sev_cbit_parameters()` reads the C-bit position and reduced physical address bits
  from CPUID `Fn8000_001F` via `/dev/cpu/0/cpuid`, falling back to the documented
  EPYC values. QEMU validates this against the host and fails loudly with the real
  value, so a stale default cannot silently weaken anything.

- `QemuCommand.create()` / `.for_measurement()` — one factory assembles the whole command from
  resolved inputs, owning the `PcieRootPinning` allocator so its five slot claims run in a fixed
  order no caller can reach. Slot layout lands in the DSDT and so in RTMR0; it used to depend on
  two hand-written call sites invoking four builders in the same order, with a parity test as the
  only thing holding them in step. `create()` has no optional parameters: a default would be one
  of the two shapes standing in for the other.
- `GuestContext` (`guest/context.py`) — what one launch materialized: the per-VM image copy, the
  tap device, the resolved volume paths, and the launch options. `LaunchConfig` is declared
  intent; this is materialized fact. `launch_vm(guest, host)` now takes it alongside the profile,
  so the two halves of a launch — what this machine IS and what this guest NEEDS — are the two
  arguments.
- `PassthroughSet` — the devices a command names, defaulting to the profile's. A `--no-gpus`
  debug launch passes an empty set: the GPUs stay on their host driver, so naming them would
  build a command QEMU refuses. A value rather than a boolean because the launch set will not
  always equal the profile's — with IB passthrough a launch attaches the VFs binding creates.
- `QemuCommandBuilder` + `LaunchCommandBuilder` / `MeasurementCommandBuilder`
  (`guest/qemu.py`) — one traversal builds both commands. The base owns the walk, its order and
  its single `PcieRootPinning`; subclasses choose only what string goes in each slot. Both sit in
  one file so the seven differences between a launch and a measurement are diffable without
  opening another.
- `guest/context.py` — `GuestContext`: what one launch materialized (per-VM image copy, tap
  device, resolved volume paths, launch options). The host half is `HostProfile`; these are the
  two arguments `QemuCommand.create` takes.
- A byte-exact regression lock on measurement generation: `tests/measurement/golden/` (8 hardware
  classes x `launch_args` + `measure_args` + `metadata`) with
  `scripts/update_measurement_golden.py` to regenerate deliberately. `metadata` is the COMPLETE
  input to the tdx-measure fork, so an unchanged dict proves MRTD and RTMR0 cannot have moved —
  without the fork or Docker. Regeneration is a script, never a side effect of running tests.
- `guest/privileged.py`, `guest/volumes.py`, `guest/images.py`, `guest/network.py` — stage 3 of a
  launch, one module per resource it materializes. `launch.py` drops from 742 lines to the four
  stages plus config resolution.
- `config.cli_fields()` — the config model owns each setting's CLI flag via
  `json_schema_extra={"cli": "--flag"}`, so the parser arguments and the CLI-over-YAML overlay are
  both derived from it. Shared sub-models name their children's flags on the parent, because
  `VolumeSpec` is used by both cache and storage and the flag is a function of (parent, field).

### Changed

- `paths.firmware_path()` takes an optional firmware filename instead of hardcoding the
  TDVF. It defaults to `GUEST_FIRMWARE`, so every existing caller is unchanged; the
  SEV-SNP path passes the AMD build. Still not overridable by user config — the TDX MRTD
  and the SEV-SNP launch digest are both computed over these exact bytes.
- `build_base_cmd()` takes the `HostProfile` rather than sixteen scalars, and derives the
  platform from it. This fixes a real bug: guest NUMA was decided in two places —
  `HostProfile.uses_guest_numa` from the captured profile, and `len(host_nodes) >= 2`
  from live sysfs. A host whose live NUMA disagreed with its profile got PXB bridges
  pinned from one and flat memory args from the other, a command matching no
  measurement. The profile decides, and a contradiction now raises.
- `QemuCommand.tdx_guest` renamed to `tee_object`, keeping its TDX default so anything
  constructing a `QemuCommand` directly — offline measurement generation included — is
  unchanged and does not start depending on the generating host's hardware.
- SEV-SNP guests are launched with `memory-backend-memfd,share=on` (private memory is
  served from guest_memfd, so `memory-backend-ram` fails), `vmport=off` per NVIDIA's
  confidential-computing deployment guide, policy `0x30000` with the DEBUG bit clear,
  and `kernel-hashes=on` so the kernel/initrd/cmdline hashes land in the launch
  measurement — which is what makes the measured-initrd LUKS key release gate work.

- **One reading of the host per launch.** Step 0 reads the profile once and hands it to both
  the preflight check and the boot primitive. Previously `discover-profile.sh` ran twice — once
  inside preflight, which signed *that* profile and got the launchable verdict for it, and again
  in the boot primitive, which built the QEMU command from a different read. Nothing tied the two
  together, so the control plane could approve a shape that never booted.
- **The host profile is a required argument, never a default.** `launch_vm`, `run_preflight` and
  `_signed_profile` all take it; a default would only ever be a second reading that could disagree
  with the one being signed, so the shape makes that unrepresentable. The commands whose job
  *starts* with reading a host — `host verify`, `host submit-profile` — take their reading at
  their own entry point via `preflight._read_host()`.
- **One execution path for the boot primitive.** `chutes-cvm guest launch` calls
  `__main__.launch_vm(args, host)` directly instead of re-entering `__main__.main()`, which
  flattened a resolved `LaunchConfig` into an argv list only for argparse to parse it straight
  back. `main(argv)` is now purely the standalone `python -m chutes_cvm.guest` debug entry and
  reads the host itself, because nothing handed it one. `build_parser()` is exposed so the
  orchestrator still gets argparse's defaults filled rather than hand-building a Namespace.

- **The argv round-trip is gone.** `_boot()` used to flatten a resolved `LaunchConfig` into
  `["--image", ...]` purely so the boot primitive's argparse could parse it straight back into a
  Namespace. It now builds a `GuestContext` and calls `launch_vm` directly.
- `iommufd` is derived from the passthrough set rather than appended separately. Every endpoint
  `build_pci_topology` emits carries `iommufd=iommufd0`, so a command with passthrough devices and
  no such object is one QEMU refuses — the two are one decision. This also fixes a latent bug: the
  *measurement* command emitted endpoints referencing an object it never declared, surviving only
  because `ImageConfig` swaps every `vfio-pci` for a `pci-bar-stub` before anything runs it.
- `PciTopologyState.add_device()` takes the endpoint string rather than a host BDF. It owns
  placement — port, slot and function allocation, identical whatever is being placed — while what
  gets placed is the caller's. The BDF was never enough for both: a `pci-bar-stub` is built from
  the device's vendor, class and captured BARs, and the BDF is meaningless on the generating box.
- **The offline measurement command is built, not rewritten.** `ImageConfig` used to take a
  launch-shaped command and substitute seven things over it (220 lines); it now renders an
  already dump-shaped one (67). The old direction was brittle one way only, and it was the
  dangerous way: anything added to the launch command that `ImageConfig` did not know to strip
  silently entered the bytes the fork hashes. Deriving the `iommufd` object did exactly that, and
  only the golden lock caught it.
- `guest/__main__.py` renamed to `guest/vm.py`. `__main__.py` is a magic filename meaning "what
  `python -m` runs"; once that stopped being true, the QEMU process's start/kill/status had no
  business living there.
- `QemuCommand.serial` is a rendered field and `tee_object` is nullable, so a command can express
  a null serial and no confidential guest — what the dump machine needs, and what `ImageConfig`
  used to hardcode.
- `PciTopologyState.add_device()` takes the endpoint string rather than a host BDF. It owns
  placement (port, slot, function — identical whatever is placed); what gets placed is the
  caller's. The BDF was never enough for both: a `pci-bar-stub` is built from the device's vendor,
  class and captured BARs, and the BDF is meaningless on the generating box.
- `guest launch` no longer accepts abbreviated flags (`allow_abbrev=False`). With prefix matching
  on, a mistyped or removed flag silently resolves to whatever it is a prefix of, and adding a
  flag can break automation by making an abbreviation it relied on ambiguous.

### Removed

- `launch._tee_active()`. A third, weaker copy of the platform check: it hardcoded the two kvm
  parameter paths instead of using the module constants, accepted `"Y"` only for TDX while
  accepting `"Y"` or `"1"` for SNP (so a TDX host reporting `1` was refused at Step 0 and
  accepted two steps later), and fell back to `/proc/cpuinfo`, which reports CPU capability
  rather than KVM enablement — the exact false positive `detect_host_tee` exists to avoid.
  Its `sudo dmesg` fallback was a root-requiring restatement of the sysfs read.
- `HostProfile.tee`. It was a platform string with exactly one caller — a stringly-typed
  comparison in the launcher — and it was never serialised, so it was neither schema nor
  fingerprint. `tee_provider` is the class's platform identity, and
  `TeeProvider.verify_environment()` is the capability check; a bare string beside them
  could only disagree with them.
- `get_tee_provider()`. Detection-based provider selection with no production caller,
  left over from before the platform was derived from the profile's CPU vendor.

- `cpu_args` override. `build_base_cmd` accepted one and no production caller passed it; the
  profile is the authority, resolved from the QEMU version. A test existed asserting the launcher
  never used it, which is a parameter whose coverage is "assert it is never passed".
- `guest/__main__.py`'s `main()`, its argparse parser, and the `--clean`/`--ssh` flags that
  existed only for them. That was the interface the former quick-launch.sh called across the
  bash->Python boundary; the shim now forwards to the CLI, so it had no callers left — and it was
  a second way to boot a guest that skipped the preflight attestation gate and force-killed a
  running VM without asking. `chutes-cvm guest launch` is now the only path to a guest.
- `guest launch --config`. It shared the positional's dest and never worked: an optional
  positional applies its default even when it matches zero arguments, so it clobbered whatever
  the flag set and the launch silently fell back to the default config path. Use the positional
  (`chutes-cvm guest launch config.yaml`), which is what the docs and ansible already use.
- `ImageConfig._endpoint_for` / `_swap_endpoint` and their regexes, `_reserve_off`, `_bars_arg`,
  and the five module-level command builders. `_endpoint_for` was 35 lines parsing `rp3` back
  into "the third GPU" to recover BARs the builder was holding all along — constructing forward
  never loses them.
- `_CLI_TO_SECTION` / `_CLI_TO_VOLUME`, and the 18 `add_argument` calls they shadowed. Every flag
  was declared twice, so one added to the parser and forgotten in a table silently did nothing.

### Fixed

- `build-firmware.sh` reached `edksetup.sh` with the caller's positional parameters still
  set. A sourced script inherits `"$@"`, so any flag passed to `build-firmware.sh` arrived
  at edksetup as an unknown option, whereupon it printed usage and returned *without*
  configuring the build environment. `--secure-boot` had been broken this way all along;
  only the no-argument default ever worked.
- The "no confidential-computing platform enabled" error listed the AMD BIOS switches
  even when it fired on an Intel host. The per-platform remedy now lives on the provider.
- **`network.ssh_port` never reached the boot primitive.** It is a config field and a
  `--ssh-port` flag, but `_boot`'s argv carried no `--ssh-port`, so the primitive's own parser
  default (10022) won and an operator's setting was silently ignored for user-mode networking.
  `GuestContext` carries it by construction. Tap-mode launches are unaffected, and nothing
  measured changes — netdevs are not PCI devices and do not reach the DSDT.
- **The measurement command was invalid.** It emitted `vfio-pci` endpoints carrying
  `iommufd=iommufd0` while declaring no such object — a command QEMU refuses. It survived only
  because `ImageConfig` replaced every endpoint with a `pci-bar-stub` before anything ran it. The
  object is now derived from the passthrough set, so the two cannot be set apart.
