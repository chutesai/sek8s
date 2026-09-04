### Fixed

- The initramfs hook now stages every binary its boot scripts use. `fetch_key` copied `curl`, `jq`,
  `openssl` and friends but not `head`, `tr`, `cut`, `sed`, `awk` or `wc` — those were present only
  because an unrelated package hook happened to stage them. Removing the unused `overlayroot` hook
  took them away, and the guest failed to boot with
  `fetch_key_and_unlock: line 153: head: not found`, aborting the LUKS unlock. Ubuntu initramfs
  ships klibc-utils rather than busybox, which provides none of them.

  A build-time check now lists the produced initramfs and fails the build if any required binary is
  absent, so this class of breakage surfaces at build time instead of at boot.

