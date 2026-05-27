### Added

- `ansible/guest/roles/rtmr3-measure/files/tdx-measure-miner.conf` and `tdx-measure-gpu.conf`: extended RTMR3 measurement coverage to additional filesystem paths not previously included:
  - `/usr/lib/systemd/system`
  - `/etc/fstab`
  - `/var/spool/cron/crontabs`
  - `/etc/init.d`
  - `/etc/rc.local`
  - `/root/.bashrc`, `/root/.bash_profile`, `/root/.profile`
- `tdx-measure-gpu.conf` aligned to the same measurement tiers as `tdx-measure-miner.conf`: systemd unit dirs, ld.so config, modprobe, sysctl, profile, environment, fstab, crontabs, init scripts, root shell startup files, and the `/usr/local/bin`, `/usr/local/sbin`, `/usr/bin`, `/usr/sbin`, `/usr/local/lib` binary tiers.
