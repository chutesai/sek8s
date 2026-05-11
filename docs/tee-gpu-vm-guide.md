# TEE GPU VM Guide

This guide covers everything you can do inside a Chutes TEE GPU VM: verifying the
hardware security posture, inspecting TDX measurements, attesting the GPU, and
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
RTMR[3]           a7c3f2d1e8b9...   # Access config: SSH keys, passwd, sudoers, grub
MRSEAM            d82f1c4a7e9b...   # Intel TDX module measurement
MRSIGNERSEAM      c3a7f2d1e8b9...   # Intel TDX module signing key
TEE TCB SVN       0400000000...     # TDX security version number
```

RTMR3 is particularly significant: it contains the boot-time measurement of the VM's
access configuration (SSH keys, user accounts, sudo rules, console policy). Use
`verify-access-config` to interpret this value in human-readable form and confirm a
PASS (see [Access configuration verification](#access-configuration-verification)).

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

## Access configuration verification

### Why this matters

The VM image is built by the host operator and delivered to you with your SSH public
key already installed. Because the VM's root disk is not encrypted at rest, a
malicious operator could theoretically modify the disk image offline — for example,
adding an additional SSH key or re-enabling console access — without your knowledge,
giving them a backdoor into your session.

To prevent this, every file and setting that controls access to the VM is measured
into **RTMR3** — a hardware-protected TDX Runtime Measurement Register — during
initramfs at every boot, before any userspace process can run. These measurements
are reflected in the TDX attestation quote, which is signed by the CPU and cannot
be forged.

The files measured at boot include:

| Path | What it controls |
|---|---|
| `/etc/ssh/` | SSH host keys and daemon configuration |
| `/etc/pam.d/` | PAM authentication stack for all services including SSH |
| `/root/.ssh/authorized_keys` | SSH public keys granted root access |
| `/etc/passwd` | User account definitions |
| `/etc/shadow` | Password hashes |
| `/etc/sudoers`, `/etc/sudoers.d/` | Privilege escalation rules |
| `/etc/default/grub` | Boot cmdline — includes kernel-level console masking |
| `/usr/local/bin/verify-access-config` | This verification script itself |

The script itself is measured, so any tampering with the verification tool also
changes RTMR3 — the tool cannot be silently replaced. Additionally, the expected
SHA-384 hashes of canonical files are embedded in the initramfs at build time (covered
by RTMR1). If any of these files are tampered with offline, the VM powers off rather
than booting with a compromised state.

### Running the verification

```bash
verify-access-config
```

The tool does the following in order:

1. **Session info** — current time, last boot time, and a reminder to note this value
   (see [Boot time and session continuity](#boot-time-and-session-continuity))
2. **SSH authorized keys** — fingerprint and comment for every authorized key
3. **SSH daemon configuration** — authentication settings, permitted login methods
4. **PAM SSH authentication stack** — active PAM auth entries for the SSH service
5. **Console access configuration** — GRUB cmdline masking and live systemd service
   state for all getty/serial console services; fails if any console is actively running
6. **User accounts and sudo rules** — all accounts with interactive shells and all
   sudo privilege grants
7. **Password status** — lock state for every account in `/etc/shadow`
8. **RTMR3 replay** — computes the expected RTMR3 from the current on-disk state
   using the same deterministic SHA-384 extend sequence the initramfs used at boot,
   then reads the live RTMR3 from a fresh TDX quote and compares

### Interpreting the result

**`✓ PASS`** — The files on disk today are byte-for-byte identical to what was present
when the VM booted, and no interactive console service is running. The SSH keys
displayed are the only keys with access. No offline modification has occurred.

**`✗ FAIL (RTMR3 mismatch)`** — The current filesystem does not match what was
measured at boot. A file in the measured set was modified after the image was built.
The output includes per-file SHA-384 hashes to help identify which file changed.
**Treat the VM as compromised and do not continue the session.**

**`✗ FAIL (console active)`** — An interactive getty or serial console service is
running. This should never happen on a correctly built image. **Treat the VM as
compromised and do not continue the session.**

**`ERROR`** — The tool could not complete the comparison (e.g. missing TDX quote
generator, non-TDX environment). This is a configuration issue, not a security alert.

### Recommendation

Run `verify-access-config` at the **start of every session** before conducting any
sensitive work. RTMR3 is re-measured from scratch on every boot, so a fresh PASS
after each reboot gives you a new hardware-backed guarantee that the access
configuration is exactly what was built into the image.

### Boot time and session continuity

RTMR3 tells you **what** was measured (the access files were untampered) but not
**when** the VM was booted. The boot time shown by `verify-access-config` is read
from the kernel and is **not** cryptographically attested.

Note the boot time when you first connect:

```bash
uptime -s        # e.g.  2026-05-11 10:30:45
```

If this value changes between checks, the VM was rebooted during your session. A
reboot is not itself a security event — RTMR3 will be re-measured from the same
image and a fresh `verify-access-config` PASS will confirm the access configuration
is unchanged. However you should:

1. Re-run `verify-access-config` after the reboot to get a fresh PASS
2. Note whether the reboot was expected or announced by the operator
3. Re-unlock your LUKS storage volume if one was set up (`luks-setup open`)

A reboot with a **different** boot time followed by a `FAIL` from
`verify-access-config` is the strongest signal of a potential tampering event.

## Storage encryption

`luks-setup` sets up LUKS2 full-disk encryption on the storage volume attached to
the VM. It handles the one-time setup (wipe, encrypt, format, mount) and the
per-session unlock after reboot. No unlock credentials are stored on the VM — the
volume must be explicitly unlocked with your passphrase each time the VM starts,
ensuring only you can access the data.

Must be run as root (already the case in this VM).

The storage volume is identified automatically — you do not need to know the raw
device name. The standard mount point is `/data`.

### First-time setup

```bash
luks-setup setup
```

You will be asked to confirm the wipe, then shown a generated 128-character hex
passphrase. **Save this passphrase immediately** — it is not stored anywhere on
the system and is required every time you unlock the volume after a reboot. Store
it in a password manager before confirming.

The command will:
1. Generate a cryptographically secure passphrase and display it
2. Auto-detect and unmount the storage device
3. Create a LUKS2 container using the generated passphrase
4. Format the encrypted volume as XFS
5. Mount it at `/data` for this session

Output summary:

```
Auto-detected storage device: /dev/vdb
Setup complete.

  Device:       /dev/vdb
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
  device:  /dev/vdb
  ...
```

Check the mount:

```bash
lsblk
```

```
NAME      MAJ:MIN RM   SIZE RO TYPE  MOUNTPOINTS
vdb       252:32   0  2000G  0 disk
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
exist to provide full transparency into what the VM connects to during your session
— no connections are hidden.
