### Added

- CI installs `apparmor-utils` and generates `en_US.UTF-8` before the unit tests. Both
  close a silent gap rather than adding new checks: without `apparmor_parser` the five
  AppArmor profile-compile cases `pytest.skip`, so a profile that will not load ships
  green — and a profile that will not load is boot-fatal, since
  `verify-apparmor-profiles.service` powers the guest off when a sek8s profile is missing
  from the loaded set. Without a non-C locale the RTMR3 collation test cannot detect its
  own regression: runners default to `C.UTF-8`, which collates in byte order, so the
  expected ordering holds whether or not `tdx-measure` still pins `LC_ALL=C`.
