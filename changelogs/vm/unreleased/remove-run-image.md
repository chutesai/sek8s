### Removed

- `guest-tools/scripts/run-image.sh`: dead, unreferenced libvirt/VNC/cloud-init
  test-boot script predating the current `run-td` TDX launch flow (pointed at
  long-gone `tdx-guest-*-final.qcow2` / `build-server-image.sh`).
