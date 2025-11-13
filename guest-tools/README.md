# Intel TDX Measurement Guide

## Reproducible MRTD & RTMR Generation for Boot-Time Attestation

This guide explains how to generate **deterministic boot measurements** (MRTD and RTMR0–3)
for Intel TDX guests. These measurements are used by the attestation server to verify the
integrity of the VM *before releasing the LUKS decryption key*.

The workflow extracts all boot-chain components, dumps ACPI tables, and computes the
exact boot measurements using Intel's `tdx-measure`.

This documentation applies to your TDX environment using:

* Custom TDVF (`firmware/OVMF.fd` or `firmware/TDVF.fd`)
* QEMU-based launch
* qcow2-based rootfs image
* initramfs-based attestation flow

---

## 📁 Directory Structure

Recommended layout:

```text
measure/
  extract-vm-measurements.sh   # Extract kernel/initrd/cmdline from qcow2
  extract-acpi.sh              # Dump ACPI tables using QEMU+TDVF
  compute-measurements.sh      # Run tdx-measure to compute MRTD/RTMR
  metadata.json                # Static description of measurement inputs

  boot/
    vmlinuz                    # Extracted kernel
    initrd.img                 # Extracted initramfs
    cmdline.txt                # Extracted kernel arguments

  acpi/
    acpi-tables.dtb            # Dumped ACPI tables

firmware/
  TDVF.fd                      # Committed TD-enabled OVMF firmware (your OVMF.fd)
```

Each measurement depends **only** on these inputs.

---

## 🧬 Intel TDX Measurement Overview

TDX defines two key measurement concepts:

### MRTD — TD Root Measurement Digest

Immutable measurement created at TD build/initialization time.
Includes (conceptually):

* TDVF (TD-enabled OVMF)
* Early TD memory layout
* ACPI tables
* Other immutable boot-critical structures

This value must match exactly what the attestation server expects.

---

### RTMRs — Runtime Measurement Registers (0–3)

Runtime measurement registers extended during boot and (optionally) runtime:

| RTMR  | Typical Contents                                    |
| ----- | --------------------------------------------------- |
| RTMR0 | Early boot / firmware-related extensions            |
| RTMR1 | Kernel + initramfs + ACPI                           |
| RTMR2 | Kernel command line                                 |
| RTMR3 | Runtime IMA/file measurements (optional, post-boot) |

For your LUKS gating flow, RTMR0–2 are the important ones at **boot time**.

---

## 🖼️ Architecture Diagram — Boot Measurement Pipeline

```text
                          ┌──────────────────────────────────────┐
                          │         Build-Time Pipeline           │
                          └──────────────────────────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │ 1. extract-vm-measurements.sh       │
                    │--------------------------------------│
                    │ Extract from qcow2:                  │
                    │   • vmlinuz                          │
                    │   • initrd.img                       │
                    │   • cmdline.txt                      │
                    └─────────────────────────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │ 2. extract-acpi.sh                  │
                    │--------------------------------------│
                    │ Run QEMU+TDVF in paused mode:       │
                    │   • TDX enabled                     │
                    │   • Kernel/initrd loaded            │
                    │ Dumps:                              │
                    │   • acpi/acpi-tables.dtb            │
                    └─────────────────────────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │ 3. metadata.json                    │
                    │--------------------------------------│
                    │ Defines all measurement inputs:      │
                    │   • TDVF.fd                          │
                    │   • boot/vmlinuz                     │
                    │   • boot/initrd.img                  │
                    │   • boot/cmdline.txt                 │
                    │   • acpi/acpi-tables.dtb             │
                    └─────────────────────────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │ 4. compute-measurements.sh          │
                    │--------------------------------------│
                    │ Runs: tdx-measure                   │
                    │ Output: expected-measurements.json   │
                    │   • MRTD                             │
                    │   • RTMR0–3                          │
                    └─────────────────────────────────────┘
                                      │
                                      ▼
                          ┌──────────────────────────────────────┐
                          │     Attestation Server (Runtime)     │
                          │--------------------------------------│
                          │ Loads expected-measurements.json     │
                          │ Receives TDX Quote from VM           │
                          │ Compares:                            │
                          │   MRTD   (firmware / immutable boot) │
                          │   RTMR0  (early boot)                │
                          │   RTMR1  (kernel/initrd/ACPI)        │
                          │   RTMR2  (cmdline)                   │
                          │ If all match → release LUKS key      │
                          └──────────────────────────────────────┘
```

---

## 🧩 Prerequisites

Make sure the following tools are installed on the build/measurement host:

* **guestfish** (from `libguestfs-tools`)
* **QEMU** with TDX support (`qemu-system-x86_64`)
* **tdx-measure** (from Intel’s `tdx-tools`)
* **jq** (optional, for inspecting JSON)

Example (Ubuntu):

```bash
sudo apt install qemu-system-x86 libguestfs-tools jq
# tdx-measure comes from Intel tooling (built or installed separately)
```

---

## 🚀 Step 1 — Extract Boot Artifacts

`extract-vm-measurements.sh` extracts the boot components from the qcow2 that your TD actually uses.

Run:

```bash
./measure/extract-vm-measurements.sh path/to/guest.qcow2
```

This script:

* Mounts the qcow2 image read-only via `guestfish`
* Detects the first matching kernel and initramfs in `/boot`
* Extracts:

  * `measure/boot/vmlinuz`
  * `measure/boot/initrd.img`
  * `measure/boot/cmdline.txt` (parsed from `/boot/grub/grub.cfg`)

These three files must correspond exactly to what the VM uses at boot. They drive **RTMR1** (kernel/initrd) and **RTMR2** (cmdline).

---

## 🖥️ Step 2 — Dump ACPI Tables

`extract-acpi.sh` uses QEMU and your TDVF to generate ACPI tables **without fully booting the guest**.

Run:

```bash
./measure/extract-acpi.sh
```

This script:

* Starts QEMU with:

  * `-machine q35,...,confidential-guest-support=tdx`
  * The committed TDVF (`firmware/TDVF.fd` or equivalent)
  * The extracted kernel/initrd/cmdline from `measure/boot/`

* Requests QEMU to dump ACPI into a DTB:

  * `measure/acpi/acpi-tables.dtb`

These ACPI tables are part of the boot measurement and influence both **MRTD** and **RTMR1**.

---

## 📦 Step 3 — `metadata.json`

`metadata.json` ties all these inputs together in the format `tdx-measure` expects.

A typical example (adjust paths to match your repo layout):

```json
{
  "boot_config": {
    "bios": "firmware/TDVF.fd",
    "acpi_dtb": "acpi/acpi-tables.dtb"
  },
  "kernel": {
    "image": "boot/vmlinuz",
    "initrd": "boot/initrd.img",
    "cmdline": "boot/cmdline.txt"
  }
}
```

Notes:

* This file is **effectively static** as long as:

  * TDVF
  * kernel
  * initrd
  * cmdline
  * ACPI DTB

  don’t change.
* Regenerate inputs and re-run `tdx-measure` whenever any boot component changes.

---

## 📏 Step 4 — Compute Expected MRTD & RTMRs

`compute-measurements.sh` is a thin wrapper around `tdx-measure`.

Example content:

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

tdx-measure \
  --json-file metadata.json \
  --json > expected-measurements.json

echo "Wrote expected measurements to expected-measurements.json"
```

Run:

```bash
./measure/compute-measurements.sh
```

This generates:

```text
measure/expected-measurements.json
```

Example structure:

```json
{
  "MRTD":  "9cbd30ffe17306d9e59523bd1cb0e6c4...",
  "RTMR0": "fea32e4d92ce48bb93db51d876f218...",
  "RTMR1": "48cc21a287f981a3581bdab9ddd45e...",
  "RTMR2": "9816bcb911e9ff6e8c60b0143e7867...",
  "RTMR3": "000000000000000000000000000000..."
}
```

These values are what your **attestation server** will enforce.

---

## 🔐 Step 5 — Using These Values in Attestation

At boot, your initramfs:

1. Generates a TDX Quote (via `tdx-attest` or equivalent)

2. Sends the Quote to your attestation server

3. The server:

   * Parses MRTD and RTMR[0..3] from the Quote
   * Loads `expected-measurements.json` (or values from your DB)
   * Compares:

     * `quote.MRTD`  vs `expected.MRTD`
     * `quote.RTMR[0]` vs `expected.RTMR0`
     * `quote.RTMR[1]` vs `expected.RTMR1`
     * `quote.RTMR[2]` vs `expected.RTMR2`

4. If all required values match and the platform TCB is acceptable:

   * **Return the LUKS decryption key** to the initramfs

5. If any mismatch:

   * **Do not release the key**
   * Log, alarm, or fail boot

This ensures the encrypted root volume is only ever decrypted for a VM that matches the known-good boot chain you’ve pre-measured.

---

## 📌 When to Regenerate Measurements

You must re-run the full pipeline if any of the following change:

* TDVF firmware (`firmware/TDVF.fd`)
* Guest kernel (new vmlinuz)
* Initramfs content (new initrd)
* Kernel command line
* QEMU version (changes ACPI layout/content)
* Bootloader or initramfs construction affecting measured paths

Each change requires:

1. Re-running `extract-vm-measurements.sh`
2. Re-running `extract-acpi.sh`
3. Re-running `compute-measurements.sh`
4. Updating your attestation policy store with new MRTD/RTMRs

---

## ✔ Best Practices

* **Commit all measurement inputs**:

  * `firmware/TDVF.fd`
  * `measure/boot/vmlinuz`
  * `measure/boot/initrd.img`
  * `measure/boot/cmdline.txt`
  * `measure/acpi/acpi-tables.dtb`
  * `measure/metadata.json`

* Treat this directory as the **attestation recipe** for your VM image.

* Use CI to:

  * Run the extraction scripts
  * Run `tdx-measure`
  * Validate that expected measurements match what’s in your policy DB

* Keep TDVF, kernel, initramfs, and QEMU versions **pinned** or tightly controlled.

---

## 🧪 Inspecting the Measurements

To inspect the resulting measurements:

```bash
jq . measure/expected-measurements.json
```

Extract specific fields:

```bash
jq -r .MRTD measure/expected-measurements.json
jq -r .RTMR1 measure/expected-measurements.json
```

You can use these values directly in logs, dashboards, or policy definitions.

---

## 🎉 Summary

By following this process, you get:

* A **deterministic, reproducible** measurement of your TDX guest boot chain

* A clear mapping from:

  * TDVF / kernel / initramfs / cmdline / ACPI

  → **MRTD & RTMR0–2**

* A strong attestation gate for LUKS decryption:
  the root filesystem is only ever unlocked if the VM is exactly the build you intend to trust.
