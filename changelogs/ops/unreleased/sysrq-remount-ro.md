### Fixed

- `force_reboot.yml`'s SysRq sequence now remounts filesystems read-only (`u`) between the sync
  (`s`) and the reset (`b`). `s` flushes what is dirty at that instant but leaves every
  filesystem mounted read-write, so anything written in the seconds before `b` lands on a
  filesystem the reset then abandons mid-write. `u` closes that window, and is the standard
  sync/remount/reboot order for an emergency reset.
