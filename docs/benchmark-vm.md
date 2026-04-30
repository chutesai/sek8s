# Benchmark VM

Operator reference for building and launching the benchmark VM. For the partner-facing
walkthrough, see [benchmark-guide.md](benchmark-guide.md). For the development debug
build, see [debug-mode.md](debug-mode.md).

## Overview

Benchmark mode builds a specialised guest image for NDA partner evaluation sessions.
The image runs the full Chutes GPU stack without Kubernetes orchestration, provides
SSH access for the partner, and ships the `attest` and `luks-setup` tools along with
a host-side network logger that records all external connections for transparency.

## What changes vs. a production image

| Behaviour | Production | Benchmark |
|-----------|-----------|-----------|
| LUKS encryption | yes | no |
| SSH access | no | partner keys only |
| K3s / Kubernetes | yes | no |
| Chutes orchestration services | yes | no |
| `attest` tool | no | yes |
| Host network logging | no | yes (`benchmark-netlog`) |
| Image suffix | `.qcow2` | `-benchmark.qcow2` |

Benchmark mode implicitly applies all debug-mode skips (no LUKS, no access hardening)
so `debug_build` does not need to be set separately.

## Building the image

### 1. Set inventory variables

Edit `ansible/guest/inventory.yml`:

```yaml
all:
  vars:
    benchmark_build: true
    benchmark_ssh_keys:
      - "ssh-ed25519 AAAA... partner@example.com"
      # add more keys as needed
```

`benchmark_ssh_keys` is required — the build will fail if it is empty so that an
image with no partner access is never shipped.

### 2. Build

```bash
cd ansible/guest
ansible-playbook -i inventory.yml playbooks/site.yml
```

The resulting image is written to:

```
<img_dir>/<build_env>/<vm_version>-benchmark.qcow2
```

### 3. Reset inventory after building

Set `benchmark_build: false` and clear `benchmark_ssh_keys` before any subsequent
non-benchmark builds.

## Launching the VM

Use `quick-launch.sh` with the `--benchmark` flag:

```bash
cd host-tools/scripts
cp config/config.benchmark.example.yaml config.yaml
# Edit config.yaml — at minimum set network.public_interface and network.vm_ip
./quick-launch.sh config.yaml --benchmark
```

The `--benchmark` flag:
- Sets the default base image to `tdx-guest-benchmark.qcow2`
- Skips cache and config volume setup
- Auto-installs and starts the `benchmark-netlog` service on the host
- Skips miner credential validation (only hostname is checked)

### Example config

See [`host-tools/scripts/config/config.benchmark.example.yaml`](../host-tools/scripts/config/config.benchmark.example.yaml)
for a ready-to-use template.

## In-VM attestation: `attest`

The `attest` tool is installed at `/usr/local/bin/attest` and runs inside the benchmark
VM. It requires membership of the `tdx-attest` group (the root user qualifies by default).

### Commands

#### `attest dump`

Generates a TDX quote and prints the VM's hardware measurements — MRTD, RTMRs,
MRSEAM, MRSIGNERSEAM, and TEE TCB SVN. No external services are contacted.

```
$ attest dump
Field            Value
──────────────────────────────────────────────────────────────────────
MRTD             a3f2...
RTMR[0]          00000...
RTMR[1]          4c8b...
RTMR[2]          f19d...
...
```

Use `--json` for machine-readable output:

```bash
attest dump --json
```

#### `attest verify`

Performs full attestation:

1. **TDX measurement dump** — same as `attest dump`
2. **GPU attestation** — sends GPU evidence to NVIDIA's Remote Attestation Service
   (NRAS). Returns an ES384-signed JWT that can be independently verified against
   NVIDIA's public certificates.
3. **TDX remote verification** — verifies the TDX quote against Intel Tiber Trust
   Services. Requires `/etc/tdx-attest-config.json` with a valid Intel API key;
   skipped gracefully if the file is absent.

```bash
attest verify
# or with a custom Intel config:
attest verify --tdx-config /path/to/tdx-attest-config.json
# or JSON output:
attest verify --json
```

### Intel TDX config

`/etc/tdx-attest-config.json` is partner-provided and is not baked into the image.
The file format is defined by Intel's `trustauthority-cli`. If absent, `attest verify`
still completes GPU attestation and prints a clear message explaining that TDX remote
verification was skipped.

## Storage encryption: `luks-setup`

`luks-setup` is installed at `/usr/local/bin/luks-setup` and must be run as root.
It provides two subcommands:

### `luks-setup setup`

One-time setup: wipe, LUKS2-encrypt, format (XFS by default), and mount for the
current session. No entries are written to `/etc/crypttab` or `/etc/fstab` — the
volume must be explicitly unlocked via `luks-setup open` after each reboot.

```bash
luks-setup setup /dev/vdb /data
# with a custom label:
luks-setup setup /dev/vdb /data --label mydata
# with ext4 instead of XFS:
luks-setup setup /dev/vdb /data --fs ext4
# skip confirmation prompt:
luks-setup setup /dev/vdb /data --yes
```

### `luks-setup open`

Open and mount an already-encrypted device (manual use or recovery):

```bash
luks-setup open /dev/vdb /data
```

## Host-side network logging

When launched with `--benchmark`, `quick-launch.sh` installs and starts the
`benchmark-netlog` systemd service on the host. It uses `conntrack` to stream all
connection events for the VM's bridge subnet, writing them to daily log files:

```
/var/log/chutes/benchmark-netlog/netlog-YYYYMMDD.log
```

Logs rotate daily, compressed after one day, and retained for 90 days.

### Useful commands

```bash
# View live connections
journalctl -u benchmark-netlog -f

# Check today's log
tail -f /var/log/chutes/benchmark-netlog/netlog-$(date +%Y%m%d).log

# Service status
systemctl status benchmark-netlog
```

The bridge subnet logged can be overridden via `/etc/chutes/benchmark-netlog.env`:

```ini
BRIDGE_SUBNET=192.168.100.0/24
```

## Security notes

- Benchmark images contain the partner's SSH public keys as the **only** authorised
  keys. The builder's cloud-init keys are removed during the cleanup phase.
- The build asserts that `authorized_keys` contains exactly the keys in
  `benchmark_ssh_keys` — count and content — and halts if the assertion fails.
- Benchmark images have no LUKS encryption. Treat the image file as sensitive; delete
  it after the evaluation session.
- `benchmark_build: true` should never be set when building production or miner images.
