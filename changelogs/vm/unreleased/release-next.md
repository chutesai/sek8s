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

- `nvidia-lkca.conf` is written unconditionally. The guard was `when: not stat.exists`, which tests
  existence rather than content, so a zero-byte file left by earlier build state satisfied it and
  the real content was never written — leaving ECDSA/ECDH not preloaded before `nvidia.ko`. A
  cached build shipped exactly that (its hash was the SHA-384 of the empty string). `copy` with
  inline content is already idempotent, so the guard only ever suppressed repair.
