### Changed

- Every file the guest build downloads is pinned by SHA-256 as well as version: the k3s install
  script and binary, helm, OPA, cosign, the Intel SGX repository key, NVIDIA's cuda-keyring and
  the root signing public key (`root_signing_key_sha256`; rotating the key is now a commit).
  A changed or tampered upstream asset now fails the build instead of being measured into the
  image (or, for the repository keys, trusted for every package after it).
- The build writes the image-set manifest before computing measurements, since
  `measurements generate` now checks the guest firmware against the one the manifest records.
- Root-filesystem measurement is named for what it does on both platforms: the role and initramfs
  script are `rootfs-measure` (formerly `rtmr3-measure`), the post-mount check is `rootfs-verify` and
  `rootfs-verify.service` (k3s `Requires=` it), the canonical manifest is `/etc/rootfs-manifest`
  (formerly `/etc/tdx-rtmr3-expected-hashes`) and the SEV-SNP verified hash is
  `/run/sek8s/rootfs-digest`. RTMR3 remains TDX's register; only its extend is TDX-specific.
- The measured-file walk is `tee-measure` (`/usr/local/bin/tee-measure`, configured by
  `/etc/tee-measure.conf`, source `tee-measure-miner.conf`), formerly `tdx-measure`: it hashes the
  root on both platforms. Unrelated to the `tdx-measure` fork that computes MRTD/RTMR0, which keeps
  its name.
- SEV-SNP guests verify the root filesystem at boot. `rootfs-measure` hashes the same measured
  paths on both platforms; TDX extends RTMR3 with the final hash as before, while SNP, which has
  no RTMR3, requires it to equal the hash of the canonical manifest baked into the measured
  initramfs and powers off otherwise (a file added, removed or changed offline, or a missing
  manifest). The verified hash is left in `/run/sek8s/rootfs-digest`, and `rootfs-verify` re-checks
  the root against it after the bind mounts. Previously both steps skipped on SNP.
- The initramfs stages `printf` explicitly (`fetch_key` hook) and the build fails if it is absent.
  `tee-evidence` builds the SEV-SNP report request with it (`\xHH` escapes); it was only present
  because busybox happened to be pulled into the initramfs, and without it production SNP boots
  would fail closed while debug images, whose attestation is fail-open, booted.

### Fixed

- Quote and GPU-evidence nonce validation (`sek8s.nonce`, `chutes_nvevidence.util`) requires
  exactly 64 hex characters. It used `bytes.fromhex`, which skips whitespace between byte pairs,
  so a 64-character nonce with a gap decoded short of 32 bytes (refused downstream regardless).
