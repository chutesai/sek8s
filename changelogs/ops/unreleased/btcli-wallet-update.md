### Added

- Host Ansible (`chutes_vm_config`) reads the hotkey file's `privateKey` into `config.yaml` as
  `miner.private_key`, so hotkeys from current Bittensor (some with no `secretSeed`) can launch a
  VM. The private key is taken when the file has one and `secretSeed` only otherwise, and
  `config.yaml` carries just that one key. Explicit `chutes_miner_private_key` joins
  `chutes_miner_seed` as an override.

### Changed

- Host Ansible fails with one clear message when the credentials are missing or ambiguous: a
  hotkey file with neither `privateKey` nor `secretSeed`, or both keys set explicitly.
- A host relaunched with the new host Ansible moves to the private key, which needs a guest image
  from this release. The example configs and guides show `private_key` as the primary key and
  `seed` as the supported alternative.
