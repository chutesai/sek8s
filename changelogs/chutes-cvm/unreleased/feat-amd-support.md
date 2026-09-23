### Added

- `firmware/OVMF.amdsev.fd` is pinned in the repo alongside `OVMF.inteltdx.fd`, with
  `firmware/PROVENANCE.md` recording its origin and digest, and `build-firmware.sh
  --amd-sev` to rebuild it from edk2. The SEV-SNP launch measurement is a hash of
  these exact bytes, so resolving firmware from `/usr/share/ovmf` would let a distro
  package update silently invalidate every published measurement.
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

### Fixed

- `build-firmware.sh` reached `edksetup.sh` with the caller's positional parameters still
  set. A sourced script inherits `"$@"`, so any flag passed to `build-firmware.sh` arrived
  at edksetup as an unknown option, whereupon it printed usage and returned *without*
  configuring the build environment. `--secure-boot` had been broken this way all along;
  only the no-argument default ever worked.
- The "no confidential-computing platform enabled" error listed the AMD BIOS switches
  even when it fired on an Intel host. The per-platform remedy now lives on the provider.
