### Changed

- `chutes_vm_config` role: `base_image` and `overlay_directory` in `config.yaml` are now driven by `chutes_vm_base_image` and `chutes_vm_overlay_directory` Ansible variables (both default to `""`, preserving the existing behaviour of letting host-tools use its built-in defaults).
