"""Offline SEV-SNP launch-measurement generation.

The AMD counterpart to this package's RTMR work, and far smaller. SEV-SNP produces a
single 48-byte launch digest covering the firmware image, the CPUID/secrets pages and --
because the launcher sets ``kernel-hashes=on`` -- a measured page holding the hashes of
the kernel, initrd and cmdline. There is no event log and no runtime register, and
(unlike RTMR0) no dependence on guest RAM, PCI topology or ACPI content: two hardware
classes differing only in their GPUs produce the *same* digest. That is expected, not a
bug -- ``expected_gpus``/``gpu_count`` still gate policy on the verifier side.

The input set is therefore just the firmware bytes, the vCPU count, the vCPU identity and
the direct-boot artifacts. Guest policy is deliberately NOT among them: it travels in the
attestation report and is checked there (see the DEBUG bit), but it is not hashed into
the launch digest.

Like the tdx-measure fork this is pure computation -- no SEV-SNP hardware is required, so
a single build host generates both platforms' measurements for a release.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from chutes_cvm.measurement.runtime_rtmr import MeasurementError

SNP_MEASURE_BIN = "sev-snp-measure"


def cpu_fms_from_processor_id(processor_id: str | None) -> tuple[int, int, int]:
    """Decode (family, model, stepping) from a fingerprint's ``cpu_processor_id``.

    The exact inverse of ``detection.detect_host_cpu_identity``, which packs CPUID
    leaf-1 EAX into the first four bytes (little-endian). Only that half is used here:
    the EDX half is a fixed TDX baseline constant and means nothing on AMD.

    The VMSA the PSP measures is seeded with this identity, so it is a measurement
    input every bit as much as the firmware. Refuses a missing id rather than falling
    back to the generating host's CPU -- the same stance ``measurement_cpu_args`` takes
    for RTMR0, and for the same reason: the fallback silently produces a plausible
    measurement for the wrong machine.
    """
    if not processor_id:
        raise MeasurementError(
            "fingerprint carries no cpu_processor_id; the SEV-SNP launch measurement is "
            "bound to the vCPU family/model/stepping, so generating without it would "
            "emit a measurement for the generating host's CPU. Re-register a host of "
            "this class (`chutes-cvm host submit-profile`) before generating."
        )
    try:
        raw = bytes.fromhex(processor_id)
    except ValueError:
        raise MeasurementError(f"malformed cpu_processor_id: {processor_id!r}")
    if len(raw) < 4:
        raise MeasurementError(f"cpu_processor_id too short: {processor_id!r}")

    eax = int.from_bytes(raw[:4], "little")
    stepping = eax & 0xF
    base_model = (eax >> 4) & 0xF
    base_family = (eax >> 8) & 0xF
    ext_model = (eax >> 16) & 0xF
    ext_family = (eax >> 20) & 0xFF
    # The encoder always folds the extended fields in, so the decode does too; for a
    # family below 0xF both extended halves are zero and this is the identity.
    return base_family + ext_family, (ext_model << 4) | base_model, stepping


def direct_boot_artifacts(image: str) -> tuple[str, str, str]:
    """``(kernel, initrd, cmdline)`` staged beside ``image``.

    Same ``<image-without-ext>.{vmlinuz,initrd,cmdline}`` convention RTMR1/2 use, so
    both platforms measure the identical bytes the launcher boots.
    """
    base = os.path.splitext(os.path.abspath(image))[0]
    kernel, initrd, cmdline_file = base + ".vmlinuz", base + ".initrd", base + ".cmdline"
    for f in (kernel, initrd, cmdline_file):
        if not os.path.isfile(f):
            raise MeasurementError(
                f"missing direct-boot artifact {f} — run stage-boot-artifacts first"
            )
    # $(cat file) semantics: drop trailing newlines from the staged cmdline.
    return kernel, initrd, Path(cmdline_file).read_text().rstrip("\n")


def compute_snp_measurement(
    image: str,
    firmware: str,
    vcpus: int,
    processor_id: str | None,
    *,
    sev_snp_measure_bin: str = SNP_MEASURE_BIN,
) -> str:
    """The SEV-SNP launch digest for this image on this hardware class.

    Returns bare uppercase hex (96 chars), the same width and shape as an RTMR so it
    drops straight into a measurements.yaml entry.
    """
    if not os.path.isfile(firmware):
        raise MeasurementError(
            f"guest firmware {firmware} not found — the launch digest is a hash of these "
            "exact bytes, so the pinned firmware must be present (see firmware/PROVENANCE.md)"
        )
    family, model, stepping = cpu_fms_from_processor_id(processor_id)
    kernel, initrd, cmdline = direct_boot_artifacts(image)

    cmd = [
        sev_snp_measure_bin,
        "--mode", "snp",
        "--vcpus", str(vcpus),
        "--vcpu-family", str(family),
        "--vcpu-model", str(model),
        "--vcpu-stepping", str(stepping),
        "--ovmf", firmware,
        "--kernel", kernel,
        "--initrd", initrd,
        "--append", cmdline,
        "--output-format", "hex",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise MeasurementError(
            f"{sev_snp_measure_bin} not found on PATH — it is a chutes-cvm dependency; "
            "install the package or pass --sev-snp-measure-bin"
        )
    if result.returncode != 0:
        raise MeasurementError(
            f"{sev_snp_measure_bin} failed ({result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )

    measurement = (result.stdout or "").strip().splitlines()[-1].strip().upper()
    if len(measurement) != 96 or not all(c in "0123456789ABCDEF" for c in measurement):
        raise MeasurementError(
            f"{sev_snp_measure_bin} returned an unexpected measurement: {measurement!r}"
        )
    return measurement
