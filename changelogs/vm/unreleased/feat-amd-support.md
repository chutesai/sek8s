### Added

- The guest image boots on both Intel TDX and AMD SEV-SNP hosts. `load-tee` replaces
  `load-tdx` in the initramfs: it tries `tdx_guest`, then `sev-guest`, modprobes
  `tsm_report` and mounts configfs, so one image serves both platforms.
- udev rule granting the attestation service group access to `/dev/sev-guest`,
  mirroring the existing `tdx_guest` rule. The device is root-only by default and the
  service does not run as root, so without this the SNP evidence path fails with
  EACCES at report generation.

### Changed

- The shared initramfs libraries are named for the boot phase they serve: `attest-common` is
  now `init-premount-common` and `provision-common` is `init-bottom-common`. Each is the flow its
  prod and debug entry scripts share; `tee-evidence` and `hotkey-sign` are the libraries shared
  between the two phases.
- The initramfs detects the TEE and produces its evidence (a TDX quote, or the raw SEV-SNP
  report through configfs-TSM) in one shared `tee-evidence` library, used by both boot
  attestation and the init-bottom `/provision` call. Both send it in the API's `quote` field; the
  API tells the platforms apart by the bytes. `/provision` previously always ran
  `tdx-quote-generator`, so a production SEV-SNP guest failed it and powered off. The TEE check
  moved from function entry to after network setup, so a guest that cannot reach the API fails
  with a network error rather than a misleading TEE error.
- The initramfs refuses a nonce or certificate hash that is not exactly 64 hex characters
  before building the report data, instead of cutting the pair to 128 characters, which shifted
  the certificate hash and left the API to reject the evidence without saying why.
- `rtmr3-measure` exits successfully on SEV-SNP. RTMR3 is an Intel runtime
  measurement register and SNP has no equivalent — its single launch digest is fixed
  when the VM starts. TDX keeps its fail-closed behaviour, and a guest with *neither*
  device still fails closed, so a TDX guest whose module failed to load cannot be
  mistaken for SNP and let through unmeasured.
- `nvidia-tdx.service` is renamed `nvidia-tee.service` ("TEE GPU setup"). It only runs
  `nvidia-smi conf-compute -srs 1` to mark the CC GPUs ready, which works unchanged
  under SEV-SNP (verified on 8x RTX PRO 6000).

- `run-vm` boots the `start_img_path` it is given instead of choosing between checkpoints
  itself, so a playbook's checkpoint rules live in the playbook: `base-image.yml` passes
  `prepare-image`'s output, and the GPU builds get theirs from `resume-checkpoint`.

### Fixed

- The k3s role fetches the k3s installer from the k3s release tag matching the pinned binary
  (`raw.githubusercontent.com/k3s-io/k3s/<k3s_version>/install.sh`) instead of `get.k3s.io`, with
  retries. `get.k3s.io` serves whatever script is current, so the files it installs could differ
  between builds, and while it returned HTTP 500 every build failed at the k3s step.
- A guest build always starts from a fresh build VM. `run-vm` used to restart any `tdx-build` VM
  and disk an earlier run left behind unless `NO_CACHE` was set, so a rerun after a failure carried
  on from that run's half-applied state instead of from the image the playbook chose. It now
  discards them and copies the starting image (base image or checkpoint) in every time.
- Production builds failed on a fresh Ubuntu 26.04 build host at `prepare-boot-image`'s root
  backup: LUKS encryption copies the root out and back with `rsync`, which the 26.04 server image
  no longer ships and nothing installed. `virt-host-prereqs` now installs it. Debug builds never
  reach that step, so a host that had only built debug images never showed it.
- The guest build's GPU checkpoint is named by a hash of what produced it instead of by guest
  version. Each playbook declares its checkpoint — a name and the input files (roles, handlers,
  vars) that decide its contents — to the new `resume-checkpoint` role, which names it
  `<name>-<inputs hash>.qcow2` and starts the build from it only if one exists for those inputs.
  Re-running a role on a reused checkpoint only adds, so anything the role stopped installing
  survived into the image: after the `nvidia-tdx` → `nvidia-tee` rename, rebuilds still shipped
  `nvidia-tdx.service` until the checkpoint was deleted by hand. `tee-gpu-vm.yml`, whose
  checkpoint also includes `common`, no longer shares a checkpoint name with
  `chutes-miner-vm.yml`. Saving a checkpoint removes older ones of the same name.
- `rtmr3-verify` now gates on the TEE exactly as `rtmr3-measure` does: verify on TDX,
  skip on SEV-SNP, fail closed with neither device. Only the initramfs half had the
  gate, so an SNP guest reached the TDX quote path, found no RTMR3, and failed (a
  production build would power itself off; a debug build crashed on `None.hex()`).
  Either way the unit failed, and k3s — which `Requires=` it — never started.

### Notes

- The skip above is a real reduction in what a SEV-SNP guest attests: the runtime file
  measurements have no SNP counterpart. Closing that gap needs dm-verity (putting
  root-filesystem integrity in the launch digest) or a vTPM at VMPL0 — both deliberate
  design decisions rather than something to paper over at boot.
- Ubuntu 24.04's GA 6.8 kernel ships no `sev-guest` module, so an SNP guest must run
  the HWE kernel, which provides both configfs-TSM and `/dev/sev-guest`.
