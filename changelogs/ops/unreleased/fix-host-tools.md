### Fixed

- `nvidia-gpu-tools` (and `chutes-reset-gpus`) now self-heal after a host OS
  upgrade that changes the system Python (e.g. 25.10 → 26.04, Python 3.13 →
  3.14). `ensure_gpu_tools_available()` verifies the CLI actually runs instead
  of trusting its presence on `PATH`, and rebuilds the bundled-wheel venv when
  it was built for a different Python version. Previously the orphaned venv left
  the CLI broken with `ModuleNotFoundError: No module named 'entry_point'` — and
  re-running `setup-tdx-host` did not fix it because the stale symlink still
  resolved on `PATH`.
