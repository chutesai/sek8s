### Fixed

- **The guest image is now byte-reproducible: two `NO_CACHE` builds produce identical
  MRTD/RTMR1/RTMR2/RTMR3.** It was not before, and had not been for a long time — two of the five
  causes below sit in paths measured since well before the RTMR3 path list was expanded. Because
  the RTMR3 expected-hashes manifest is baked into the initramfs, RTMR3 drift drags RTMR2 with it,
  so both registers moved on every rebuild. Found by diffing `/etc/tdx-rtmr3-expected-hashes`
  between two builds, which names every measured file whose hash changed.
  - **Base image was verified but not pinned.** `releases/<ver>/release/` is a rolling path
    Canonical repoints at each respin, and the checksum was fetched from that same rolling path —
    which proves integrity ("we got what is served now") but not reproducibility. `/usr/lib`,
    `/usr/bin` and `/usr/sbin` are ~80% of the ~49,600 measured files and come overwhelmingly from
    the base image, so a respin moved 263 files under `/usr/lib/firmware` alone. Now pinned via
    `base_image_url` + `base_image_sha256`, with the hash as the source of truth so a dated
    Canonical respin, an R2 mirror, or a local path are interchangeable.
  - **`/etc/shadow` carried the build date.** Field 3 is days-since-epoch of the last password
    change, stamped on every system account a package install creates (`usbmux`, `dnsmasq`,
    `redis`, `nvidia-persistenced`), so builds on different days differed. Now normalized to a
    constant immediately before the manifest is generated. Distinct from the root password hash,
    whose salt was already pinned — this is the date column, not the hash.
  - **SSH host keys are regenerated per build.** Production deletes them during cleanup (sshd is
    removed there), so measuring `/etc/ssh` as a directory is stable there — and directory coverage
    catches anything unexpected appearing in it, so production keeps it. Debug keeps sshd, so the
    keys stay and moved RTMR3 on every rebuild; debug builds now narrow that one entry to the SSH
    *policy* files. Host keys are server identity, not access policy. Deleting them in debug instead
    is not an option: sshd will not start without them, and regenerating at boot drifts RTMR3 on the
    second boot — `RTMR3_VERIFY_DEBUG_MODE` downgrades the local poweroff to a warning, but the
    validator still rejects a VM whose RTMR3 does not match its registered value.
  - **DKMS signed each kernel module with a per-build Machine Owner Key.** `shim-signed` generates
    `/var/lib/shim-signed/mok/MOK.priv` randomly on first use, so all 20 `nvidia*.ko.zst` across
    two kernels differed every build. Signing is now disabled (`try_sign_modules="false"`, written
    before the driver install so no signed module is ever produced). The signatures verified
    nothing: the guest direct-boots with no Secure Boot chain and no `module.sig_enforce`. RTMR3
    measuring the modules is strictly stronger than a signature from a key regenerated per build
    and trusted by nobody, and this stops baking a throwaway private key into every image.

- **Production images shipped the build host's SSH public key.** `run-vm` authorises the build
  host's key as root so Ansible can reach the build VM, and nothing removed it: `remove-ssh` deletes
  the sshd packages but not the keys, and `lock-accounts` sweeps only uid >= 1000 homed under
  `/home`, so `/root` is excluded by design. `/root/.ssh/authorized_keys` is measured into RTMR3, so
  every published measurement was specific to the machine that built it — two builds of identical
  source on different hosts agreed on 49,366 of 49,367 measured files and disagreed on exactly that
  one, which by itself made the measurements irreproducible by anyone else. `remove-ssh` now purges
  `authorized_keys` for every account in `/etc/passwd` plus a filesystem-wide sweep (the file is
  relocatable via `AuthorizedKeysFile`), and asserts none survive. Also removes a standing root
  authorisation for the build host from every shipped image.

- The first build on a clean checkout failed when saving a checkpoint. `save-checkpoint` copies the
  build disk into `guest-tools/image/<env>/`, but `copy` does not create parent directories and only
  `guest-tools/image/` itself is tracked (via `.gitkeep`) — the per-env subdirectory is created by
  earlier builds, so the failure was invisible on any host that had built before. It now creates the
  directory, as `export-vm-image` and `prepare-image` already did. Found by running
  `inventory-reproduce.yml` on a host that had never built an image.

- **Checkpoint capture lost the guest's last writes.** `virsh suspend` pauses vCPUs but does not
  flush the guest page cache, so writes from the seconds before it never reached the virtual disk
  and were absent from the copied image — silently, since the tasks that made them reported
  `changed`. `save-checkpoint` now syncs inside the guest before suspending. The final guest image
  was never affected (it is captured from a graceful `virsh shutdown`), but every cached
  `*-gpu.qcow2` was exposed, and a build resuming from such a checkpoint carries the loss forward.
  - The base image capture is now terminal rather than a checkpoint: nothing uses that VM
    afterwards, so it is shut down cleanly and then copied, giving a properly unmounted filesystem
    instead of a crash-consistent one, and no build VM left running after the playbook ends.
  - The lost write that exposed this was `cloud-init clean`, which meant the published base image
    retained `/var/lib/cloud` — including the build host's cloud-config and SSH public key. That is
    stale state in a public artifact, and being build-host-specific it would have prevented anyone
    else reproducing the layer's bytes.

- `nvidia-lkca.conf` is written unconditionally. The guard was `when: not stat.exists`, which tests
  existence rather than content, so a zero-byte file left by earlier build state satisfied it and
  the real content was never written — leaving ECDSA/ECDH not preloaded before `nvidia.ko`. A
  cached build shipped exactly that (its hash was the SHA-384 of the empty string). `copy` with
  inline content is already idempotent, so the guard only ever suppressed repair.

### Added
- **`playbooks/base-image.yml` builds the pinned base image** — the Ubuntu cloud image plus the
  `common` package set — as a versioned, hash-pinned artifact the guest build consumes. This is the
  input the guest build could not otherwise pin: `/usr/lib`, `/usr/bin` and `/usr/sbin` are ~80% of
  the ~49,600 files measured into RTMR3 and come almost entirely from here, while the Ubuntu archive
  and the Intel SGX repo carry no version pins. Freezing them into a published artifact makes the
  guest build reproducible without requiring every upstream to stay still.
  - Separate playbook because it runs on a different clock: the base image changes when the package
    set is deliberately bumped, the guest image changes with every code change. Versioned
    independently (CalVer — the artifact is "the archive as of roughly this date"), with old
    versions retained so a measurement published months ago stays verifiable.
  - Refuses the rolling `releases/<ver>/release/` Canonical path and requires a dated respin, so the
    layer itself is rebuildable from the same bytes later.
  - Emits `.sha256` and `.provenance` sidecars. The provenance records only what is *not* recoverable
    by inspecting the image (which Canonical respin, which snapshot, which commit) — package
    contents are already inspectable via `dpkg -l` / `dpkg -V`.
- The guest build no longer runs the `common` role at all — those packages come from the base
  layer. Running it a second time would have defeated the layer: its packages are unpinned, so
  anything absent from the layer would install at whatever version the archive served that day.
  The build **fails loudly** when `base_image_version`/`base_image_sha256` are unset rather than
  falling back to an unpinned upstream fetch; a silent fallback produces a different image with
  different measurements while looking like a normal success.
- The base image is fetched with `aria2c -x 16` instead of a single HTTP stream. One stream caps at
  roughly 30 MB/s to the CDN regardless of available bandwidth, so the 6.7GB layer took 3m40s on
  every `NO_CACHE` build. The pin is still enforced (`--checksum=sha-256=`), and a cached image is
  hashed first so a matching one is not re-fetched and a stale one is replaced. `file://` and other
  transports keep the single-stream path, since aria2c rejects them and parallelism would not help
  a local copy.
- Intermediate cache layers (`*-prepared.qcow2`, `*-gpu.qcow2`) moved from `image/<env>/` to a
  shared `image/checkpoints/`, keyed on `vm_version` + the base image version. Nothing at or before
  the GPU checkpoint reads `build_env` or `debug_build` — the layers are identical across envs and
  variants — so a prod and a debug build now share one checkpoint instead of each building its own,
  the same way both already share one base image under `base/<version>/`. Keying on the base image
  version still prevents reusing a cache derived from a different base.
