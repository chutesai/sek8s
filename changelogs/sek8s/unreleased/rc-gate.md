### Removed
- system-manager's `ImageManager` no longer pulls images. Removed the cosign-verified pull
  path (`start_pull` / pull-status tracking, `PullStatusEnum` / `PullSnapshot`), the
  `COSIGN_PUBLIC_KEY_PATH` (`cosign_public_key_path`) setting, and the `CosignClient`
  dependency. It now only lists, deletes, and prunes containerd images; image signature
  verification stays with the admission controller.
