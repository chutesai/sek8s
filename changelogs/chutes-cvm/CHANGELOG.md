# chutes-cvm Changelog

The `chutes-cvm` CLI + toolkit (`src/chutes-cvm/`) — an independently installable host CLI
(`pip`/`install.sh`). Versioned with SemVer via `src/chutes-cvm/VERSION`. Run
`make promote-changelogs` to aggregate fragments into the current version section.
## [0.3.0] - 2026-10-10

### Added
- `miner.private_key` (`--miner-private-key`): the miner hotkey's 64-byte sr25519 private key, a
  current Bittensor hotkey file's `privateKey`, accepted in place of `miner.seed` for hotkeys
  whose file has no `secretSeed`. It is written to the config volume as `miner-private-key`, and
  the host-side shutdown and Chutes API calls sign with it.
- `chutes-cvm host platform [--check]` prints the TEE this host's CPU vendor implies (`tdx` on
  Intel, `snp` on AMD); `--check` also requires it to be enabled in KVM and exits 1 with the
  platform's BIOS remedy if not. It is what host automation branches on, so the answer comes from
  the same provider a launch uses rather than a second probe.
- `H100PcieProfile` for the H100 PCIe (`10de:2331`): CC mode only (no NVLink fabric to
  protect, so no PPCIe), 80 GB, and the API's `h100`. BARs captured from g3-h100-small-dal-1,
  with a measurement golden (`h100_pcie_amd`) for the class it derives there: `flat-28c-80g`
  under SEV-SNP.
- `profiles.profile_for_device_ids()`, the one lookup from a host's GPU device ids to its
  profile. `HostProfile.gpu_profile` and `host reset-gpus` both use it.
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
- The SEV-SNP guest object names the EPYC C-bit constants (`cbitpos=51`,
  `reduced-phys-bits=1`). QEMU refuses a launch whose `cbitpos` differs from the host's, and
  neither value is measured: live launches with `reduced-phys-bits` 1 and the hardware's 5
  measure identically.
- `QemuCommand.build(host, context)` — one traversal assembles every command, owning the
  `PcieRootPinning` allocator so its five slot claims run in a fixed order no caller can reach.
  Slot layout lands in the DSDT and so in RTMR0; it used to depend on two hand-written call sites
  invoking four builders in the same order, with a parity test as the only thing holding them in
  step.
- `GuestContext` (`guest/context.py`) — everything one guest needs to build its command: image,
  firmware, guest NUMA nodes, boot artifacts, network, volumes, devices and process, plus the
  eight environment leaves (machine, memory backend, `-cpu`, guest object, serial, emitted
  devices, passthrough endpoints, iommufd) that differ between a launch and the offline dump.
  `LaunchContext` (with `TdxLaunchContext` / `SnpLaunchContext`) is a real guest on the TEE host,
  built by `LaunchContext.from_host()` from Step 1's measured image (or None for a test boot),
  the per-VM image copy, the tap device and the resolved volume paths; `MeasurementContext.from_host()` is the dump's placeholder guest. The
  host half is `HostProfile`, so `launch_vm(guest, host)` takes the two halves of a launch.
- `PassthroughSet` — the devices a command names, defaulting to the profile's. A `--no-gpus`
  debug launch passes an empty set: the GPUs stay on their host driver, so naming them would
  build a command QEMU refuses. A value rather than a boolean because the launch set will not
  always equal the profile's — with IB passthrough a launch attaches the VFs binding creates.
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
- Offline SEV-SNP launch-measurement generation (`measurement/snp.py`), computed
  in-house rather than by shelling out to `sev-snp-measure`: the SEV-SNP ABI's PAGE_INFO digest
  chain over the firmware image, the pages its SEV metadata declares, the kernel/initrd/cmdline
  hashes page and one VMSA per vCPU. Inputs are the pinned firmware, the image's staged
  direct-boot artifacts, and the class's vCPU count and CPUID signature (from its fingerprint's
  `processor_id`). Verified byte-exact against live attestation reports from two hosts that
  differ in every input: 8x RTX PRO 6000 on EPYC 7763 (252 vCPUs, our firmware) and H100 on
  EPYC 9124 (28 vCPUs, the Ubuntu firmware); both are pinned as tests.
- `measurement.snp.AcpiTables` derives a host class's expected ACPI hash offline, the value the
  SEV-SNP firmware checks the guest's tables against: SHA-256 over `etc/table-loader` and each
  blob it allocates, each framed as `name[56] || u64 LE size || bytes`. The tables come from the same
  `--create-acpi-tables` dump RTMR0 uses. Verified byte for byte against a live 8x RTX PRO 6000
  SNP guest (pinned as a test).
- Each SEV-SNP host class is measured with its expected ACPI hash on the cmdline
  (`sek8s.acpi_sha256=<hash>`, composed by `SnpLaunchContext.with_acpi`), and the
  generator records it beside `measurement` as `acpi_sha256`. A measurement is only ever computed
  with a real hash.
- An SEV-SNP launch boots `sek8s.acpi_sha256=<hash>` on its kernel cmdline: the hash published
  for its image on this host class (`SnpLaunchContext.acpi_sha256`), so the firmware checks the
  guest's ACPI tables against it. A test boot passes `unverified` instead, which the firmware (`03-acpi.patch`)
  accepts without checking the tables. The value is in the measured cmdline, so such a guest
  matches no published measurement and cannot attest.
- `guest/host_class.py`: `HostClass` is this host's class as Chutes records it, fetched once
  from its platform's route (POST /servers/{tdx,snp}/host_profiles/status) and parsed by its
  platform's subclass:
  `TdxHostClass` lists `MeasuredImage` entries, `SnpHostClass` lists `SnpMeasuredImage` entries
  carrying their required `acpi_sha256`. An SNP entry without a valid hash fails at retrieval.
  `measured_image(image)` is the launchability join against an `ImageSet`, raising `NotMeasured`.
- `HostProfile.tee_provider` is the host's platform, taken from the captured CPU vendor when the
  profile is built (an unsupported vendor is refused there); the profile builds its guest object
  through it (`guest_object()`). The provider class is the platform's only identity:
  `TeeProvider.for_cpu_vendor()` and `TeeProvider.enabled_on_host()` return provider types, and
  `host platform` prints the provider's `name`.
- **`chutes-cvm host devices-free`** — exits 0 if nothing holds this host's passthrough
  devices, 1 otherwise, printing each reason. The same `device_blockers()` a launch checks,
  exposed because the callers that need it are not all launches: `host reset-gpus` must not SBR
  devices a QEMU still holds, and host Ansible must not open the guest image while one holds
  its write lock. Note the exit sense is the conventional one (0 means OK), unlike
  `vfio-wedged`, which exits 0 when it *finds* a problem; matching that here would make every
  `if` around it read backwards.
  The checks behind it stay individually callable, because which one applies is
  context-dependent: "does anything hold the devices?" (a running *or* reclaiming QEMU) is
  what a launch, a rebind and an SBR need, while "is a guest *serving*?" — where a reclaiming
  QEMU counts as no — is the right question for deciding whether to drain pods or skip a
  shutdown step.
- A `pages_4k` reading of 0 was reported as "the counter is not draining -- treat it as
  stalled". Verified on an 8x RTX PRO 6000 SNP host, that is the normal case there and the
  advice was backwards: SNP zeroes the counter the moment the guest powers off and then hands
  the memory back over the next few minutes, invisible to it. An operator was being told to
  reset a host with three minutes left. Zero now reads as "this counter cannot tell you",
  with the advice to poll `host devices-free`; "stalled" is reserved for pages that remain
  and do not move. `read_reclaim` also returns immediately on a zero reading instead of
  sleeping out its sample window, which a polling caller would otherwise pay per iteration.
  Measured for the record: SNP reclaimed 465GB in 4m38s (~1.7GB/s), roughly 25x faster per
  byte than TDX, so the hours-long case is TDX-specific. The state itself — a zombie leader
  with one live thread holding every device — is not, and the detection is verified on both.

### Changed
- The launch config takes exactly one of `miner.private_key` or `miner.seed`, as plain hex
  without `0x` (128 and 64 characters). Both set, a `0x` prefix or a wrong length are refused when
  the config is loaded, before anything launches; the guest refused these at boot.
- Host-side signing refuses a key that does not belong to `miner.ss58`. The Chutes API calls used
  to warn and sign with the key's own hotkey, which the API then rejected.
- `chutes-cvm guest launch --help` describes every flag. Config flags take their help from the
  `LaunchConfig` field descriptions (the cache/storage/config volume flags prefixed with which
  volume), so a flag and its help cannot drift apart.
- `chutes-cvm host setup` is platform-aware. A recipe is now one OS version for one platform:
  the abstract `Ubuntu2604Recipe` holds what every 26.04 host needs (CUDA repo, QEMU,
  `nohibernate`, the nouveau blacklist), and `TdxUbuntu2604Recipe` / `SnpUbuntu2604Recipe`
  extend it. TDX adds the Intel SGX repo, `ovmf-inteltdx`, the PCCS/QGS/QPL attestation
  packages, `kvm_intel.tdx=1` and the PCCS/QGS/QCNL configuration step (`configure_attestation`,
  now in `host/tdx_attestation.py`); SEV-SNP adds nothing — Ubuntu 26.04's kernel enables it
  from BIOS alone. `resolve_recipe` picks by OS version and the CPU vendor's platform, so an
  unsupported pair fails before setup touches the host. Previously an AMD host was given the
  full Intel stack.
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
- **The host profile is a required argument, never a default.** `launch_vm` and
  `_signed_profile` both take it; a default would only ever be a second reading that could disagree
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
- SEV-SNP guests launch flat (one memory backend, no `-numa`, no PXB grouping, no vCPU pinning)
  even on 2-node hosts. `HostProfile.uses_guest_numa` now also requires
  `TeeProvider.supports_guest_numa`, which is false for SNP: under SNP every memory accept is a
  hypervisor page-state change, the kernel extends an accept one 2 MB unit past a node's end,
  and QEMU 10.2.1's `kvm_convert_memory()` rejects a conversion spanning two guest_memfd
  backends, wedging the guest during NUMA init. Reproduced on a 2-socket EPYC 7763 host at
  every node size tried. TDX is unaffected (it accepts pages without a hypervisor exit) and
  keeps guest NUMA. AMD 2-node classes are fingerprinted `flat-…` accordingly. To be lifted once
  the pinned QEMU carries "accel/kvm: Fix kvm_convert_memory() calls crossing memory regions".
- `measurements generate` covers both platforms in one pass, since one guest image boots on
  both. A version's entry has a `tdx:` section (`mrtd`, `rtmr1`, `rtmr2`, `rtmr3`,
  `hardware[].rtmr0`) and an `snp:` section (`hardware[].measurement`); each class lands in
  exactly one, by CPU vendor (Intel = TDX, AMD = SEV-SNP). Both platforms are measured every
  release, but a section is written only when it has classes: the API's TEE config refuses a
  section with no hardware, and with it the whole file. This replaces the flat per-version
  layout, so consumers of measurements.yaml (chutes-ops `teeMeasurements`, the API) must read
  the sections.
- A missing release-level SEV-SNP input (the firmware, or the image's staged direct-boot
  artifacts) fails `generate`. Loaded once per release, before any class, rather than per class,
  where it was caught as PENDING for every AMD class and a mixed release published without them
  and exited 0. A never-measured class missing an input of its own (a null
  `processor_id`) is still PENDING.
- The measurement package follows the platforms: `platform.py` holds `PlatformMeasurements` and
  what both platforms share, `tdx.py` holds `TdxMeasurements` and every TDX register (MRTD and
  RTMR0 through the fork, RTMR1/2, RTMR3 by mounting the image), and `snp.py` holds
  `SnpMeasurements` and the launch digest. `generate_measurements.py` is only the run: fetch the
  API's host classes, dispatch each to its platform, write the file. `runtime_rtmr.py` is gone,
  and RTMR3 folds through `rtmr3.compute_rtmr3`, the helper the guest runs, instead of a copy.
- `runtime_rtmr3` in measurements.yaml is now `rtmr3`, the name the API already reads, so the
  generated file and its consumer agree. Lands in the same rollout as the API side.
- `measurements generate` writes a debug build's entry as `rc: true`, read from the image set's
  manifest. A debug image (SSH, no LUKS) attests only under an rc entry, and the API refuses to
  load an rc release without an `authorized_hotkeys` allowlist, so a debug `measurements.yaml`
  merged by mistake fails loudly instead of admitting debug guests as the release.
- The image-set `manifest.json` records the SHA-256 of the guest firmware the image was built
  with (`firmware`), and `ImageSet.verify` checks chutes-cvm's firmware against it, so launch
  (`prepare_vm_image`) and `image verify` refuse any other. `measurements generate` now verifies
  the image set it measures (`verify(full=True)` against `--bios-dir`) before measuring. The
  firmware ships with chutes-cvm rather than the image and the launch measurement covers its
  exact bytes, so a mismatch booted a guest that could not attest, or measured a firmware the
  release was never built with. Sets from before the field are not checked.
- `measurements generate` emits one hardware entry per measurement, listing every host class
  that measures that way in `fingerprints: [...]` (was one entry per class with a singular
  `fingerprint`, suffixing colliding names). The API now refuses a measurement on two entries:
  a quote matches the first, so the second's GPU rules and rc gate never applied.
- The bundled measured-file walk is `scripts/tee-measure` (`paths.tee_measure_script()`,
  `rtmr3.TEE_MEASURE`), formerly `tdx-measure`, since it serves both platforms. The `tdx-measure`
  fork (`--tdx-measure-bin`) is a separate tool and keeps its name.
- **`guest launch` Step 1 decides measured launch, test boot, or refusal.** It fetches the host
  class (as `host verify` does) and looks up the image's `(version, rc)`. A measured image launches
  measured. An unmeasured image -- or no answer from the API, or an unreadable manifest -- is
  refused if it is a production image, and test-boots (boots, cannot attest) if it is a debug
  build or `--force` is passed. Behaviour change: an unpublished debug build used to be refused;
  it now test-boots. A published `rc` build still launches measured. Benchmark launches test-boot
  without asking.
- `chutes-cvm host verify` reads the typed `HostClass` instead of the raw status response, and
  reports a host it cannot read as `BLOCKED (host)` rather than as an API failure.
- `guest/preflight.py` is now `guest/chutes_api.py` and `PreflightError` is `ChutesApiError`: the
  module holds every signed request to the Chutes API, not just the launch preflight. Requests
  sign the `HostProfile` their caller read; none reads the host itself, so `host verify`'s class
  lookup and `--submit` registration describe the same reading.
- A downloaded base image is an `ImageSet` (`ImageSet.from_dir`): its manifest is read once into
  the build's `version`/`rc` and its four artifacts, and `verify(full)` replaces
  `image_set.resolve()`. The launch, `host verify`, per-VM image staging and `image verify` all
  read it; per-VM staging copies the sidecars from the verified set rather than re-deriving
  their paths.
- The offline ACPI dump runs each platform's own machine shape, derived from the launch machine:
  drop the confidential-guest object, then spell out what the platform switches off implicitly
  (`TeeProvider.implicit_machine_opts`). TDX dumps with `smm=off,pic=off`; SEV-SNP with
  `vmport=off,smm=off`, which is what an SNP guest actually boots with. The Intel measurement
  inputs are unchanged.
- The `pci_operations_wedged()` error, which is now only reachable when an earlier run already
  created the blocked unbinds, no longer says to reboot the host. A plain `reboot` hangs in
  `device_shutdown()` while those tasks exist — the advice actively made things worse. It now
  explains that the tasks clear on their own once reclaim finishes, and gives the SysRq sequence
  for resetting immediately.
- Four separate detectors answered "is a chutes-td QEMU running?", all by matching
  `/proc/<pid>/cmdline`, and all deliberately skipping zombies — so every one of them reported
  the host as idle during a reclaim, when that process holds all twelve devices.
  `launch.py`'s `_chutes_td_running()` is gone; the duplicate-VM gate now uses the same
  `comm`-based scan as the teardown gate, which also fixes it: it previously let a second VM
  launch while the first was still reclaiming. The two refusals keep separate policy — a VM
  that is merely running stays `--force`-overridable, while one mid-reclaim is not, because
  forcing past that reaches the unbind.
  Still outstanding: `scripts/devices/reset-gpus.sh` and the two `ansible/host/.../*_chutes_td.sh`
  helpers carry the same cmdline-and-skip-zombies logic. `reset-gpus.sh` is the worst of them —
  during a reclaim it would SBR-reset GPUs a live QEMU still holds. Their comments also still
  point at the now-deleted `launch.py (_chutes_td_running)`.
- `"chutes-td"` had three definitions (`guest/vm.py`, `guest/launch.py`, and the new detector),
  with `launch.py` both importing it from `vm.py` and redefining it. `guest/vm.py` is now the
  single owner of `PROCESS_NAME`, `PIDFILE` and `LOGFILE`.
- The launch precondition moved out of `bind_passthrough` and up into `launch_vm`, immediately
  before it. Binding devices is not the place to decide whether a launch may happen, and having
  it there created a `vm -> passthrough -> vm` import cycle that was the only reason the
  detection needed modules of its own.
- `scripts/devices/reset-gpus.sh` no longer carries its own copy of the detection. Its whole
  purpose is to refuse while the GPUs are in use and then SBR-reset them, and its `cmdline`
  match plus explicit zombie skip meant that during a reclaim it saw no VM and went ahead —
  SBR-resetting GPUs a live QEMU still held, which is the worst thing it can do. It now calls
  `chutes-cvm host launch-safe`, and fails closed with an honest message if the CLI is absent.

### Fixed
- `guest launch` names the guest's TEE when it reports the VM running ("AMD SEV-SNP VM running
  with PID …" rather than "TDX VM" on every host), and describes a flat guest's host memory
  placement plainly: interleaved across the GPUs' NUMA nodes, or on the one node there is.
- `chutes-cvm host reset-gpus` takes the reset mode (CC or PPCIe) from the host's GPU profile,
  the same `get_sbr_reset_args()` a launch uses. `reset-gpus.sh` kept its own device list and
  sent every card missing from it to the PPCIe reset; it now runs the arguments it is given.
- Measurement reads the staged kernel/initrd/cmdline through the launcher's own reader. The two
  readers differed: the launcher stripped all surrounding whitespace from the cmdline, the
  measurement only trailing newlines, so a cmdline file with a trailing space would have booted
  one cmdline while RTMR2 and the SEV-SNP digest hashed another.
- `measurements generate` fails when a previously measured host class (the API's
  `measured: true`) cannot be generated, listing every such class. It was reported PENDING and
  left out of the release, which exited 0 and left hosts of that class nothing to attest
  against. PENDING now means only what it means in the API: a class never measured, still in
  the generator's queue.
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
- A launch following a VM that had run a GPU workload could leave the host unable to reboot.
  When a TD powers off, QEMU does not exit: its last thread stays in `do_exit` releasing the
  guest_memfd that backs the TD's private memory, and KVM issues a SEAMCALL sequence per private
  page to block, track, cache-write-back and reclaim it. Measured on an 8xH200 host: ~39us per 4KB
  page, single-threaded, ~25.7k pages/s — a guest that had faulted in ~714GB left 187M pages and
  just over two hours of work. Throughout, that process still holds every passthrough device's
  vfio file descriptor, so `guest launch`'s stale-device unbind blocked in uninterruptible D state;
  and once those blocked unbinds exist, `reboot` hangs in `device_shutdown()` too, turning a
  teardown that would have completed on its own into a host needing a SysRq reset.
  The existing `pci_operations_wedged()` check could not prevent this: it looks for D-state tasks,
  which only exist *because* an earlier launch already fired the unbinds, so it protected the
  second attempt and never the first. `guest/vm.py` — which already owned the process — now also finds a previous one
  (`find_qemu_process`, matching `comm` and per-thread state), reads how far its reclaim has
  got (`read_reclaim`, from KVM's `pages_4k` counter), and answers whether anything still holds the
  passthrough devices (`device_blockers()`; empty means they are free). The gate fires before
  `bind_passthrough` touches anything — SR-IOV VF creation as much as the unbind — and `guest
  launch` now refuses with the remaining pages, the drain rate, an ETA, and the two ways forward:
  wait for it to finish, or reset while a reboot still works. Not force-overridable, because
  forcing past it costs a host reboot rather than a failed command.
  Detection reads `comm` and per-thread state, never `cmdline`: a zombie's
  `/proc/<pid>/cmdline` is empty, which is why every pgrep-style check reported the host as idle
  while it still held all 12 devices. A zombie whose threads have all exited has already released
  its file table and is correctly ignored. Progress comes from KVM's `pages_4k` counter under
  `/sys/kernel/debug/kvm/<qemu-pid>-<vm-fd>/`, sampled twice; it is root-only and best-effort, so
  the refusal still works without it, just without an ETA.
  Not fixable in this package: the cost tracks S-EPT *entry count*, not bytes (4KB per 39us is
  ~105MB/s against hardware good for tens of GB/s, so it is nearly all fixed per-entry overhead),
  and guest_memfd has no hugepage support on these kernels — `CONFIG_KVM_GUEST_MEMFD=y` with no
  hugepage symbol, and `-object tdx-guest` exposes no options. 2MB backing would cut the entry
  count ~512x; until it exists upstream, detecting the state and naming the escape is the fix.

### Removed
- `profiles.resolve_profile()`, unused since host profiles and `reset-gpus` resolve through
  `profile_for_device_ids()`; with one resolver, the test asserting the two agreed went with it.
- The CCEL splice-and-replay path to RTMR0 (`overrides_from_fork_log`, `mr1_events`,
  `locate_rtmr0_events`, `replay_with_overrides`, `acpi_digests`, their constants and tests). The
  fork's full RTMR0 self-generation replaced it; nothing but its own tests called it.
- `generate_measurements.DEFAULT_API_BASE`, a second copy of `paths.DEFAULT_API_BASE`.
- `measurements generate --register rtmr0`. Nothing called it: a full `generate` computes RTMR0
  inline, and the mode survived from the old pipeline, where RTMR0 was a separate step aggregated
  later. It was a second route to RTMR0 output and the only reason the TDX generator ran without
  an image. `--register rtmr3` stays; the GPU-VM build uses it.
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
- `chutes_api.run_preflight()` (POST /servers/tdx/preflight). The launch reads the class's
  measured images instead, which is also where an SEV-SNP launch gets its ACPI hash.
- `qemu.read_pci_numa_node()` and `qemu._append_numa_memory()`, unused since the command
  traversal took over device placement and guest NUMA memory.

## [0.2.1] - 2026-10-01

### Fixed
- 2-node 8x B300 hosts came up with 7 of 8 GPUs (`gpu-verify`: "expected 8 but nvidia-smi sees 7").
  Since 1.4.x every 2-node host put its GPUs behind per-NUMA-node PXB-PCIe bridges. A B300's 1 TB
  root-port window does not fit the guest's 64-bit MMIO window once it is split per bridge, so the
  last GPU's BAR2 went unassigned. B300 now opts out of PXB grouping
  (`GpuProfile.supports_pxb_grouping`). It keeps guest NUMA memory and vCPUs, with its GPUs laid
  out flat on `pcie.0` as on the flat path. The variant label for this layout is
  `numa-flatpci-<vcpus>c-<mem>g`. This changes RTMR0 for 2-node B300 host classes, whose
  measurements must be regenerated.

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

