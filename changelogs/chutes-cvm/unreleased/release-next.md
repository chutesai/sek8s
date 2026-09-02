### Changed

- **`chutes-cvm image manifest` now writes `manifest.json` next to the qcow2 by default**
  (previously `<base>.manifest.json`). `manifest.json` is the only name the readers —
  `image verify`, `image download`, and `guest launch` — look for, so a freshly generated
  set is directly consumable and copyable as a whole directory. Pass `-o` for the old name.
