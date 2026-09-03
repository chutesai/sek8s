### Fixed

- The initramfs is packed reproducibly, which was the last source of RTMR2 drift. Even with
  byte-identical content, two builds produced different archive bytes: `mkinitramfs` stages files
  with `cp -pP`, so every cpio member carries its source mtime, and those vary per build.
  Confirmed by extracting two builds' initramfs images — no differing files, no size differences,
  different hashes.

  `SOURCE_DATE_EPOCH` is now exported for `update-initramfs`. `mkinitramfs` itself never mentions
  the variable, which is misleading: it builds a sorted manifest (`LC_ALL=C sort | uniq`, so member
  ordering was already deterministic) and hands it to `3cpio --create`, and `3cpio` is what reads
  `SOURCE_DATE_EPOCH` to fix member mtimes. The `amd64_microcode` hook drives it the same way.
