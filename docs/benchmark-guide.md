# Benchmark VM Guide

This guide walks through everything you can do inside a Chutes benchmark VM: verifying
the hardware security posture, inspecting TDX measurements, attesting the GPU, and
setting up an encrypted storage disk.

## Connecting

SSH into the VM using the private key corresponding to the public key you provided
before the session:

```bash
ssh -i /path/to/your/private_key -p <ssh_port> root@<vm_ip>
```

You will land directly as `root`. All tools described in this guide are available
without any additional setup.

## GPU verification

Confirm the GPU is visible and the driver is loaded:

```bash
nvidia-smi
```

Check that Confidential Computing (CC) mode is active — this is required for
hardware attestation:

```bash
nvidia-smi conf-compute -s
```

Expected output includes `CC status: ON`. If CC mode is not active, GPU attestation
will fail.

## Attestation

The `attest` tool verifies the hardware security posture of the VM. It has two modes.

### Inspecting TDX measurements: `attest dump`

Generates a TDX quote from the hardware and prints the raw measurements of the VM's
trusted execution environment. No external services are contacted.

```bash
attest dump
```

Example output:

```
Field             Value
──────────────────────────────────────────────────────────────────────────────────
MRTD              a3f2c1d8e5b7...   # VM firmware measurement
RTMR[0]           4c8b2e9f1a3d...   # Boot environment
RTMR[1]           f19d7a4c3b8e...   # OS kernel + initrd
RTMR[2]           0000000000...     # Runtime (typically zero at rest)
MRSEAM            d82f1c4a7e9b...   # Intel TDX module measurement
MRSIGNERSEAM      c3a7f2d1e8b9...   # Intel TDX module signing key
TEE TCB SVN       0400000000...     # TDX security version number
```

These values can be compared against expected reference values to confirm the VM
has not been tampered with. Use `--json` for machine-readable output:

```bash
attest dump --json
```

### Full attestation: `attest verify`

Performs three checks in sequence:

1. **TDX measurement dump** — same as `attest dump`
2. **GPU attestation** — sends GPU evidence to NVIDIA's Remote Attestation Service
   (NRAS) and receives a signed JWT. The token is signed with NVIDIA's ES384 private
   key and can be independently verified against NVIDIA's public certificates.
3. **TDX remote verification** — verifies the TDX quote against Intel Tiber Trust
   Services (requires a config file — see below).

```bash
attest verify
```

#### Intel TDX remote verification

To enable TDX remote verification, provide a config file for Intel's Tiber Trust
Services. The format is defined by Intel's `trustauthority-cli`:

```json
{
  "trustauthority_url": "https://portal.trustauthority.intel.com",
  "trustauthority_api_url": "https://api.trustauthority.intel.com",
  "trustauthority_api_key": "<YOUR_INTEL_API_KEY>"
}
```

Save the file and pass it to `attest verify`:

```bash
attest verify --tdx-config /path/to/tdx-attest-config.json
```

If the config file is absent, TDX remote verification is skipped and a clear message
is printed. GPU attestation and measurement dump still run.

#### JSON output

All results can be captured as JSON for programmatic inspection:

```bash
attest verify --json | tee attestation-results.json
```

The JSON includes the parsed TDX measurements, GPU attestation result, and the TDX
remote verification JWT (if performed).

## Storage encryption

`luks-setup` sets up LUKS2 full-disk encryption on a storage device. It handles
everything in one command: wiping the device, creating the LUKS container, formatting,
mounting, and persisting the configuration so the volume unlocks on every reboot.

Must be run as root (already the case in this VM).

### First-time setup

Identify the device you want to encrypt:

```bash
lsblk
```

Look for an unformatted disk (no mountpoint, no filesystem type). For example:

```
NAME   MAJ:MIN RM   SIZE RO TYPE MOUNTPOINTS
vda    252:0    0    50G  0 disk /
vdb    252:16   0  2000G  0 disk
```

Here `vdb` is the storage disk. Run setup:

```bash
luks-setup setup /dev/vdb /data
```

You will be asked to confirm (all data on the device will be wiped), then prompted
to enter and confirm a passphrase. Choose a strong passphrase and keep it safe — it
will be required on every reboot.

The command will:
1. Wipe the device
2. Create a LUKS2 container with the passphrase you set
3. Format the encrypted volume as XFS
4. Mount it at `/data`
5. Add entries to `/etc/crypttab` and `/etc/fstab` for automatic unlock on reboot

Output summary:

```
Setup complete.

  Device:       /dev/vdb
  UUID:         a1b2c3d4-e5f6-...
  LUKS label:   storage
  Mapper name:  storage  (/dev/mapper/storage)
  Filesystem:   xfs
  Mount point:  /data

The volume will be unlocked automatically on next boot (passphrase prompt).
To verify encryption: cryptsetup status storage
```

### Verifying encryption

Confirm the device is open and encrypted:

```bash
cryptsetup status storage
```

Expected output:

```
/dev/mapper/storage is active and is in use.
  type:    LUKS2
  cipher:  aes-xts-plain64
  keysize: 512 bits
  key location: keyring
  device:  /dev/vdb
  ...
```

Check the mount:

```bash
lsblk /dev/vdb
```

```
NAME      MAJ:MIN RM   SIZE RO TYPE  MOUNTPOINTS
vdb       252:16   0  2000G  0 disk
└─storage 253:0    0  2000G  0 crypt /data
```

### Subsequent reboots

On reboot, the system will prompt for the passphrase to unlock the volume before
completing boot. This is handled by `/etc/crypttab` automatically.

If you need to manually open the volume (e.g. during recovery):

```bash
luks-setup open /dev/vdb /data
```

### Options

```
luks-setup setup /dev/vdb /data --label mydata   # custom label and mapper name
luks-setup setup /dev/vdb /data --fs ext4        # ext4 instead of XFS
luks-setup setup /dev/vdb /data --yes            # skip confirmation prompt
```

## Network transparency

All network connections made by the VM are logged on the host using `conntrack`.
Log files are written to daily files:

```
/var/log/chutes/benchmark-netlog/netlog-YYYYMMDD.log
```

These logs record every connection event (source IP, destination IP, protocol, port,
and timestamp) for the VM's bridge network. They are retained for 90 days.

You can request a copy of the logs from the session operator at any time. The logs
exist to provide full transparency into what the VM connects to during your evaluation
session — no connections are hidden.
