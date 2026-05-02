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

Check the confidential compute configuration — the H200 runs in Protected PCIe
(PPCIe) mode, which is required for hardware attestation:

```bash
nvidia-smi conf-compute -q
```

Expected output:

```
==============NVSMI CONF-COMPUTE LOG==============

    CC State                   : OFF
    Multi-GPU Mode             : Protected PCIe
    CPU CC Capabilities        : INTEL TDX
    GPU CC Capabilities        : CC Capable
    CC GPUs Ready State        : Ready
```

The key fields are `Multi-GPU Mode: Protected PCIe`, `CPU CC Capabilities: INTEL TDX`,
and `CC GPUs Ready State: Ready`. If the GPU is not in PPCIe mode or not ready,
attestation will fail.

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
2. **GPU attestation** — collects GPU evidence and sends it to NVIDIA's Remote
   Attestation Service (NRAS), which returns a JWT signed with NVIDIA's ES384
   private key. The token can be independently verified against NVIDIA's public
   certificates. Evidence collection uses `ppcie_mode: False` so it works
   correctly on H200s in Protected PCIe mode.
3. **TDX quote verification** — verifies the TDX quote signature using Intel's DCAP
   collateral via `dcap_qvl`. No API key or config file required.

```bash
attest verify
```

#### JSON output

All results can be captured as JSON for programmatic inspection:

```bash
attest verify --json | tee attestation-results.json
```

The JSON includes the parsed TDX measurements, GPU attestation result, and the TDX
remote verification JWT (if performed).

## Storage encryption

`luks-setup` sets up LUKS2 full-disk encryption on the storage volume attached to
the VM. It handles the one-time setup (wipe, encrypt, format, mount) and the
per-session unlock after reboot. No unlock credentials are stored on the VM — the
volume must be explicitly unlocked with your passphrase each time the VM starts,
ensuring only you can access the data.

Must be run as root (already the case in this VM).

The storage volume is identified automatically at boot and is always accessible as
`/dev/chutes-storage`. The standard mount point is `/data`.

### First-time setup

```bash
luks-setup setup
```

You will be asked to confirm (all data on the device will be wiped), then prompted
to enter and confirm a passphrase. Choose a strong passphrase and keep it safe — it
will be required on every reboot.

The command will:
1. Wipe `/dev/chutes-storage`
2. Create a LUKS2 container with the passphrase you set
3. Format the encrypted volume as XFS
4. Mount it at `/data` for this session

Output summary:

```
Setup complete.

  Device:       /dev/chutes-storage
  UUID:         a1b2c3d4-e5f6-...
  LUKS label:   storage
  Mapper name:  storage  (/dev/mapper/storage)
  Filesystem:   xfs
  Mount point:  /data

The volume is mounted for this session. After a reboot, unlock it with:
  luks-setup open
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
  device:  /dev/chutes-storage
  ...
```

Check the mount:

```bash
lsblk /dev/chutes-storage
```

```
NAME      MAJ:MIN RM   SIZE RO TYPE  MOUNTPOINTS
vdc       252:32   0  2000G  0 disk
└─storage 253:0    0  2000G  0 crypt /data
```

### After each reboot

The volume is not configured for automatic unlock — no passphrase or key is stored
on the VM. After each reboot, SSH in and unlock it yourself:

```bash
luks-setup open
```

This ensures only parties with the passphrase can access the data, even if the VM
is restarted by the host operator.

### Options

```
luks-setup setup --label mydata   # custom label and mapper name
luks-setup setup --fs ext4        # ext4 instead of XFS
luks-setup setup --yes            # skip confirmation prompt
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
