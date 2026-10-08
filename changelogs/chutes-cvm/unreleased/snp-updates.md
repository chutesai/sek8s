### Added

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

### Changed

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

### Removed

- `chutes_api.run_preflight()` (POST /servers/tdx/preflight). The launch reads the class's
  measured images instead, which is also where an SEV-SNP launch gets its ACPI hash.
- `qemu.read_pci_numa_node()` and `qemu._append_numa_memory()`, unused since the command
  traversal took over device placement and guest NUMA memory.
