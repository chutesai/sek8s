### Added

- `chutes_cvm.guest.tee`: host-side TEE abstraction covering the QEMU arguments
  that actually differ between Intel TDX and AMD SEV-SNP — the confidential-guest
  object, the memory backend type, the machine flags, and the SMBIOS product
  string. Everything else in `QemuCommand` is shared.
- `detect_host_tee()` reads the kvm module parameters (`kvm_intel.tdx`,
  `kvm_amd.sev_snp`) rather than the CPU vendor: a Genoa host with SEV-SNP
  disabled in BIOS reports AMD but cannot launch an SNP guest, and failing at
  detection is clearer than failing inside QEMU.
- `sev_cbit_parameters()` reads the C-bit position and reduced physical address
  bits from CPUID `Fn8000_001F` via `/dev/cpu/0/cpuid`, falling back to the
  documented EPYC values. QEMU validates this against the host and fails loudly
  with the real value, so a stale default cannot silently weaken anything.

- Offline SEV-SNP launch-measurement generation (`measurement/snp_measurement.py`),
  wired into `measurements generate` so **one pass over one image emits both platforms'
  measurements**. The same guest image boots on Intel and AMD, so a release should not
  need a separate generator per TEE; which measurement a host class gets follows from
  its CPU vendor (`tee_for_cpu_vendor`), since a class is Intel or AMD and never both.
  Like the tdx-measure fork it is pure computation — no SEV-SNP hardware required.
- `measurements.yaml` grows per-TEE sections under each version (`tdx:` / `snp:`).
  A version with no Intel host classes no longer computes RTMR1/2/3 at all, so an
  AMD-only release does not require the tdx-measure fork.
- `cpu_fms_from_processor_id()` derives the vCPU family/model/stepping the SEV-SNP
  VMSA is seeded with from the fingerprint's existing `cpu_processor_id` — the exact
  inverse of the packing `detect_host_cpu_identity` already does. No host-profile
  schema change and no re-registration: the field was already captured on AMD hosts.
  A missing id is refused rather than defaulted, matching `measurement_cpu_args`.
- `firmware/OVMF.amdsev.fd` is pinned in the repo alongside `OVMF.inteltdx.fd`, with
  `firmware/PROVENANCE.md` recording its origin and digest, and `build-firmware.sh
  --amd-sev` to rebuild it from edk2. The SEV-SNP launch measurement is a hash of
  these exact bytes, so resolving firmware from `/usr/share/ovmf` would let a distro
  package update silently invalidate every published measurement.

### Changed

- `build_base_cmd()` takes an optional `tee` provider. It defaults to TDX so the
  offline measurement path — which runs on ordinary build hosts with no TEE —
  keeps emitting exactly what it did before and does not start depending on the
  generating host's hardware. The launcher passes the detected provider explicitly.
- `QemuCommand.tdx_guest` renamed to `tee_object`, keeping its TDX default.
- SEV-SNP guests are launched with `memory-backend-memfd,share=on` (private memory
  is served from guest_memfd, so `memory-backend-ram` fails), `vmport=off` per
  NVIDIA's confidential-computing deployment guide, policy `0x30000` with the
  DEBUG bit clear, and `kernel-hashes=on` so the kernel/initrd/cmdline hashes land
  in the launch measurement.

### Notes

- SEV-SNP's launch digest does not depend on GPU or memory topology — only the
  firmware, the vCPU count and identity, and the kernel/initrd/cmdline. Two AMD
  hardware classes differing only in their GPUs therefore share a measurement;
  `expected_gpus`/`gpu_count` still gate them separately. Guest policy is *not* a
  measurement input either: it is carried in the report and checked there.
- `sev-snp-measure` is a new dependency of this package.
