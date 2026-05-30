### Fixed

- Add `xfsprogs` to host prerequisites so `mkfs.xfs` is available when `create-cache.sh` creates the storage volume (regression introduced in #34 when the storage volume format was switched from ext4 to XFS)
