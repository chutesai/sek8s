"""Offline SEV-SNP launch-digest generation, anchored to real hardware.

The two ``*_report_is_reproduced`` tests are the ones that matter: each reproduces the
MEASUREMENT field of a live SEV-SNP attestation report, read from inside a running guest via
configfs-tsm. They differ in every input the digest has -- firmware build, vCPU count and CPU
generation -- so together they pin the firmware parsing, the kernel-hashes page and the VMSA.

The kernel and initrd enter the digest only through their SHA-256s, so those are recorded
instead of committing the artifacts. The firmware images are fixtures rather than
``firmware/OVMF.amdsev.fd``: these tests verify the calculator, and must not start failing
because the pinned firmware is legitimately rebuilt. (Both fixture blobs are already in git
history, so they add nothing to the repository.)
"""

import hashlib
import struct
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import topology_fixtures as tf
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.measurement import snp
from chutes_cvm.measurement.platform import MeasurementError

_FIXTURES = Path(__file__).parent / "fixtures"
# Our source build (firmware/PROVENANCE.md) and the Ubuntu ovmf-amdsev 2025.11-3ubuntu7 binary.
_OVMF_OURS = _FIXTURES / "ovmf-amdsev-6e64091a.fd"
_OVMF_UBUNTU = _FIXTURES / "ovmf-amdsev-a2f54fb2.fd"

_CMDLINE = (
    "root=UUID=9a3e62be-4b82-4a7a-8f47-0b25d728fa3a ro  console=tty1 console=ttyS0"
)
# A class's expected ACPI hash, as the offline dump produces it (the 8x RTX PRO 6000 SNP class).
_ACPI = "6a8501a0f92db861ac1f055a4015adc52662da7b760463ec0331e40bb1f5f5a7"
_KERNEL_SHA256 = "d5d71ed32239eaa9bcb0528227a7adb62688250ce9f163b3e04e9675ddf86cff"

MILAN_SIG = 0x00A00F11  # EPYC 7763: family 25, model 1, stepping 1
GENOA_SIG = 0x00A10F11  # EPYC 9124: family 25, model 17, stepping 1


def _digest(firmware, *, vcpus, sig, initrd_sha256, **over):
    return (
        snp.launch_digest(
            snp.OvmfImage.load(str(firmware)),
            vcpus=vcpus,
            vcpu_signature=sig,
            kernel_sha256=bytes.fromhex(_KERNEL_SHA256),
            initrd_sha256=bytes.fromhex(initrd_sha256),
            cmdline=_CMDLINE,
            **over,
        )
        .hex()
        .upper()
    )


# ── real hardware ──────────────────────────────────────────────────────────────────────────────


def test_rtx_pro_milan_report_is_reproduced():
    """wsl-pve-2-sn64: 2x EPYC 7763, 8x RTX PRO 6000 in CC mode, 252 vCPUs, our firmware."""
    assert _digest(
        _OVMF_OURS,
        vcpus=252,
        sig=MILAN_SIG,
        initrd_sha256="789622b0c49cc0204736af0c7d60bf5685cfdf16d9dc4c0955de114bec480263",
    ) == (
        "8ED9F1D500217E4BC16F877158C8EFE60B1A9FDDEFF868DD"
        "047E4487D216C571A58ED7B743C7A332ECF46A77F88566C3"
    )


def test_h100_genoa_report_is_reproduced():
    """g3-h100-small-dal-1: 1x EPYC 9124, 28 vCPUs, the Ubuntu firmware."""
    assert _digest(
        _OVMF_UBUNTU,
        vcpus=28,
        sig=GENOA_SIG,
        initrd_sha256="09f91e46d8bc6f5e25524d302ab4fa964970a1ebdf527ba435dbfc379566cf4a",
    ) == (
        "5AC62DF8AA3865B099B233F51032131F81AFDB6595434DE4"
        "3B5129F81B08F2087716C4EF91416C95B4CFF843DE92A093"
    )


def test_every_input_moves_the_digest():
    """Each input is bound: none can change without the measurement noticing."""
    base = dict(
        vcpus=4,
        sig=MILAN_SIG,
        initrd_sha256="00" * 32,
    )
    reference = _digest(_OVMF_OURS, **base)
    variants = [
        _digest(_OVMF_UBUNTU, **base),
        _digest(_OVMF_OURS, **{**base, "vcpus": 5}),
        _digest(_OVMF_OURS, **{**base, "sig": GENOA_SIG}),
        _digest(_OVMF_OURS, **{**base, "initrd_sha256": "11" * 32}),
        _digest(_OVMF_OURS, **base, sev_features=0x21),  # + DebugSwap
    ]
    assert len({reference, *variants}) == len(variants) + 1


# ── vCPU signature from the host fingerprint ───────────────────────────────────────────────────


def test_vcpu_signature_is_leaf1_eax_from_the_processor_id():
    # The fingerprints' processor_id for the two boxes above (EAX little-endian, then EDX).
    assert snp.vcpu_signature("110fa000fffba91f") == MILAN_SIG
    assert snp.vcpu_signature("110fa100fffba91f") == GENOA_SIG


@pytest.mark.parametrize("bad", [None, "", "zz", "110f"])
def test_vcpu_signature_refuses_missing_or_malformed_ids(bad):
    """Falling back to the generating host's CPU would measure the wrong machine."""
    with pytest.raises(MeasurementError):
        snp.vcpu_signature(bad)


# ── firmware parsing ───────────────────────────────────────────────────────────────────────────


def test_ovmf_image_locates_what_the_digest_needs():
    fw = snp.OvmfImage.load(str(_OVMF_OURS))
    assert fw.gpa == (1 << 32) - len(fw.data)
    assert fw.sev_es_reset_eip == 0x80B004
    assert fw.kernel_hashes_gpa == 0x810C00
    kinds = [s.kind for s in fw.metadata_sections]
    assert snp.SECTION_SNP_KERNEL_HASHES in kinds
    assert snp.SECTION_SNP_SECRETS in kinds
    assert snp.SECTION_CPUID in kinds


def test_ovmf_image_rejects_a_non_ovmf_image():
    with pytest.raises(MeasurementError, match="not an OVMF image"):
        snp.OvmfImage(bytes(snp.PAGE_SIZE * 4))


def test_ovmf_image_rejects_a_partial_page():
    with pytest.raises(MeasurementError, match="whole number of pages"):
        snp.OvmfImage(bytes(100))


def test_missing_firmware_points_at_provenance(tmp_path):
    with pytest.raises(MeasurementError, match="PROVENANCE"):
        snp.OvmfImage.load(str(tmp_path / "absent.fd"))


# ── kernel-hashes page ─────────────────────────────────────────────────────────────────────────


def test_kernel_hashes_page_layout():
    kernel, initrd = b"\x01" * 32, b"\x02" * 32
    page = snp.kernel_hashes_page(kernel, initrd, "console=ttyS0", 0xC00)
    assert len(page) == snp.PAGE_SIZE
    assert page[:0xC00] == bytes(0xC00)

    table = page[0xC00:]
    assert uuid.UUID(bytes_le=table[:16]) == uuid.UUID(
        "9438d606-4f22-4cc9-b479-a793d411fd21"
    )
    assert struct.unpack_from("<H", table, 16)[0] == 16 + 2 + 3 * 50
    # (guid, length, sha256) x3: cmdline, initrd, kernel -- in that order.
    entries = list(struct.iter_unpack("<16sH32s", table[18:168]))
    assert [length for _, length, _ in entries] == [50, 50, 50]
    # The cmdline is hashed with its NUL, as QEMU hands it to the kernel.
    assert entries[0][2] == hashlib.sha256(b"console=ttyS0\x00").digest()
    assert entries[1][2] == initrd
    assert entries[2][2] == kernel
    # Padded to 16 bytes, and nothing after it.
    table_end = 0xC00 + 168
    assert page[table_end:] == bytes(snp.PAGE_SIZE - table_end)


def test_kernel_hashes_page_refuses_to_overflow():
    with pytest.raises(MeasurementError, match="overflows"):
        snp.kernel_hashes_page(bytes(32), bytes(32), "", snp.PAGE_SIZE - 16)


# ── VMSA ───────────────────────────────────────────────────────────────────────────────────────


def _u64(page, offset):
    return struct.unpack_from("<Q", page, offset)[0]


def test_bsp_vmsa_starts_at_the_reset_vector():
    page = snp.vmsa_page(snp.BSP_RESET_EIP, MILAN_SIG, snp.SEV_FEATURES_SNP_ACTIVE)
    assert _u64(page, 0x178) == 0xFFF0  # rip
    assert _u64(page, 0x018) == 0xFFFF0000  # cs.base
    assert _u64(page, 0x310) == MILAN_SIG  # rdx
    assert _u64(page, 0x3B0) == snp.SEV_FEATURES_SNP_ACTIVE


def test_ap_vmsa_starts_where_the_firmware_says():
    page = snp.vmsa_page(0x80B004, GENOA_SIG, snp.SEV_FEATURES_SNP_ACTIVE)
    assert _u64(page, 0x178) == 0xB004
    assert _u64(page, 0x018) == 0x800000
    assert _u64(page, 0x310) == GENOA_SIG


def test_launch_digest_needs_a_vcpu():
    with pytest.raises(MeasurementError, match="vcpus"):
        _digest(_OVMF_OURS, vcpus=0, sig=MILAN_SIG, initrd_sha256="00" * 32)


# ── from a staged image ────────────────────────────────────────────────────────────────────────


def _stage(tmp_path, cmdline=_CMDLINE + "\n"):
    (tmp_path / "final.vmlinuz").write_bytes(b"kernel")
    (tmp_path / "final.initrd").write_bytes(b"initrd")
    (tmp_path / "final.cmdline").write_text(cmdline)
    return str(tmp_path / "final.qcow2")


def test_snp_image_hashes_the_staged_artifacts_once(tmp_path):
    image = snp.SnpImage.load(_stage(tmp_path), str(_OVMF_OURS))
    assert image.kernel_sha256 == hashlib.sha256(b"kernel").digest()
    assert image.initrd_sha256 == hashlib.sha256(b"initrd").digest()
    assert (
        image.cmdline == _CMDLINE
    )  # trailing newline dropped, as the launcher's $(cat) does

    got = image.measurement(4, "110fa000fffba91f", _ACPI)
    assert len(got) == 96 and got == got.upper()
    assert got == (
        snp.launch_digest(
            snp.OvmfImage.load(str(_OVMF_OURS)),
            vcpus=4,
            vcpu_signature=MILAN_SIG,
            kernel_sha256=image.kernel_sha256,
            initrd_sha256=image.initrd_sha256,
            # The ACPI hash rides the cmdline exactly as an SEV-SNP launch appends it.
            cmdline=f"{_CMDLINE} sek8s.acpi_sha256={_ACPI}",
        )
        .hex()
        .upper()
    )
    # The class half: same release, different class, different digest.
    assert image.measurement(4, "110fa100fffba91f", _ACPI) != got


def test_platform_description_moves_the_digest(tmp_path):
    """The digest does not cover ACPI directly, so it must through the cmdline: a class whose
    tables differ (a different expected hash) gets a different measurement."""
    image = snp.SnpImage.load(_stage(tmp_path), str(_OVMF_OURS))
    a = image.measurement(4, "110fa000fffba91f", "a" * 64)
    b = image.measurement(4, "110fa000fffba91f", "b" * 64)
    assert a != b


@pytest.mark.parametrize("bad", ["unverified", "", "A" * 64, "a" * 63, "g" * 64])
def test_only_a_real_acpi_hash_is_ever_measured(tmp_path, bad):
    """A published measurement computed with the test-boot sentinel would let a host boot
    unchecked ACPI and still attest, so the generator cannot produce one."""
    image = snp.SnpImage.load(_stage(tmp_path), str(_OVMF_OURS))
    with pytest.raises(MeasurementError, match="real ACPI hash"):
        image.measurement(4, "110fa000fffba91f", bad)


def test_snp_image_needs_the_staged_artifacts(tmp_path):
    with pytest.raises(MeasurementError, match="stage-boot-artifacts"):
        snp.SnpImage.load(str(tmp_path / "final.qcow2"), str(_OVMF_OURS))


def test_snp_image_needs_the_firmware(tmp_path):
    with pytest.raises(MeasurementError, match="PROVENANCE"):
        snp.SnpImage.load(_stage(tmp_path), str(tmp_path / "absent.fd"))


# ── the platform ─────────────────────────────────────────────────────────────────────────────


def _amd_host(processor_id="110fa000fffba91f"):
    return HostProfile.from_api_profile(
        tf.host_document(
            "RTX_PRO_6000",
            vcpus=124,
            gpu_nodes=(0,) * 8,
            cpu_vendor="AuthenticAMD",
            cpu_processor_id=processor_id,
        )
    )


def _snp_platform(image):
    with patch.object(snp.SnpImage, "load", return_value=image) as load:
        platform = snp.SnpMeasurements(
            bios_dir="/fw",
            image="/img/final.qcow2",
            tdx_measure_bin="/bin/tdx-measure",
            dist="ubuntu:26.04",
        )
    return platform, load


def test_the_snp_platform_loads_the_release_inputs_once():
    _, load = _snp_platform(object())
    # The image, and the SEV-SNP firmware -- not the TDX one.
    load.assert_called_once_with("/img/final.qcow2", "/fw/OVMF.amdsev.fd")


def test_an_amd_class_is_measured_with_its_own_vcpus_and_signature():
    image = snp.SnpImage(
        firmware=snp.OvmfImage.load(str(_OVMF_OURS)),
        kernel_sha256=bytes(32),
        initrd_sha256=bytes(32),
        cmdline="",
    )
    platform, _ = _snp_platform(image)
    host = _amd_host()

    tables = MagicMock(spec=snp.AcpiTables)
    tables.sha256.return_value = _ACPI
    with patch.object(snp.AcpiTables, "dump", return_value=tables) as dump:
        entry = platform.add(host, "b" * 64)

    # The class's ACPI comes from the offline dump, run with the SEV-SNP firmware and the
    # release's dump tooling, before the digest that depends on it.
    dump.assert_called_once_with(
        host,
        firmware="/fw/OVMF.amdsev.fd",
        tdx_measure_bin="/bin/tdx-measure",
        dist="ubuntu:26.04",
    )
    assert entry["fingerprints"] == ["b" * 64]
    assert entry["acpi_sha256"] == _ACPI
    assert entry["measurement"] == image.measurement(
        host.vcpus, "110fa000fffba91f", _ACPI
    )
    assert platform.section() == {"hardware": [entry]}
