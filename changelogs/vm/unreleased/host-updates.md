### Changed
- Moved libvirt/VM lifecycle handlers from `roles/run-vm/handlers/main.yml` to the top-level `ansible/guest/handlers/main.yml` so they are available to all guest roles rather than scoped only to `run-vm`
