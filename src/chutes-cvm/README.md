# chutes-cvm

CLI and toolkit for operating Chutes confidential GPU VMs.

Installed as the `chutes-cvm` command via `install.sh` (see below). It is deliberately not
a PyPI package: it bundles the NVIDIA GPU admin tools wheel and is paired with firmware
shipped outside the package, neither of which survives a plain `pip install`. Commands cover the host
lifecycle (`host setup` / `host verify` / `host submit-profile` / `host tune`), VM launch,
and measurement generation. The library (`chutes_cvm.guest`, `chutes_cvm.host`,
`chutes_cvm.measurement`) is importable on its own; the CLI is one consumer of it.

```
pip install chutes-cvm
chutes-cvm host verify
```
