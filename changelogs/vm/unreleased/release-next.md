### Fixed

- `chute-log-shipper` can capture chute pod logs in production again. It failed with
  `[Errno 13] Permission denied: /var/log/pods/chutes_<pod>/<container>`, and the cause was the
  AppArmor profile, not the unit: the `10-security.conf` drop-in already grants
  `CAP_DAC_READ_SEARCH` so the unprivileged service can traverse kubelet's root-owned `0750` log
  dirs, but the profile never permitted the capability's use. Systemd granting a capability does
  not make AppArmor allow it. Adding `capability dac_read_search` to the profile fixes it.

  This only ever failed in production: debug images load the profile in complain mode, where the
  capability is permitted, so shipping worked there and the matching `dac_read_search` entry looked
  like harmless audit noise.
