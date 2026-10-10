"""Offline SEV-SNP launch-measurement generation.

The AMD counterpart to this package's RTMR work, and far smaller. SEV-SNP produces one 48-byte
launch digest, fixed when the VM starts, covering the firmware image, the pages the firmware's
SEV metadata declares, a measured page holding the kernel/initrd/cmdline hashes (the launcher
sets ``kernel-hashes=on``), and one VMSA per vCPU. There is no event log and no runtime
register, and the digest does not cover guest RAM, PCI topology or the ACPI tables themselves.
ACPI reaches it through the cmdline: the AmdSev firmware refuses tables whose hash differs from
``sek8s.acpi_sha256``, so each class is measured with its expected ACPI hash on the cmdline
(``AcpiTables``), the SNP counterpart of RTMR0. ``expected_gpus``/``gpu_count`` gate GPU policy
on the verifier side.

The inputs are therefore the firmware bytes, the vCPU count, the vCPU signature, the
direct-boot artifacts and the class's ACPI hash. Guest policy is deliberately NOT among them: it
travels in the attestation report and is checked there (see the DEBUG bit), but it is not hashed
into the launch digest.

Computed here rather than by shelling out to ``sev-snp-measure``: the algorithm is the SEV-SNP
ABI's PAGE_INFO chain plus a fixed VMSA layout, small enough to own and test against real
hardware reports, and not worth a third-party dependency in the measurement path. Like the
tdx-measure fork it is pure computation, so one build host generates both platforms' values.

Every constant below comes from one of: the SEV-SNP firmware ABI (PAGE_INFO, page types), OVMF's
reset-vector GUIDed table (``OvmfPkg/ResetVector``), QEMU's SEV hashes table
(``target/i386/sev.c``), or the vCPU reset state KVM and QEMU establish (Linux
``struct sev_es_save_area``). Verified against live reports from two hosts with different
firmware, vCPU counts and CPU generations -- see tests/measurement/test_snp_measurement.py.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from chutes_cvm.guest.context import MeasurementContext, SnpLaunchContext
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.qemu import QemuCommand
from chutes_cvm.guest.tee import SnpTeeProvider
from chutes_cvm.measurement import tdx
from chutes_cvm.measurement.image_config import ImageConfig
from chutes_cvm.measurement.platform import (
    MeasurementError,
    PlatformMeasurements,
    staged_boot_artifacts,
)

PAGE_SIZE = 4096
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
FOUR_GIB = 1 << 32

# ── the launch digest (SEV-SNP ABI, SNP_LAUNCH_UPDATE / PAGE_INFO) ─────────────────────────────

# Page types the PSP folds into the digest. UNMEASURED (0x4) never contributes.
PAGE_TYPE_NORMAL = 0x1
PAGE_TYPE_VMSA = 0x2
PAGE_TYPE_ZERO = 0x3
PAGE_TYPE_SECRETS = 0x5
PAGE_TYPE_CPUID = 0x6

# The PSP measures every VMSA at this fixed GPA, whatever the vCPU.
VMSA_GPA = 0xFFFFFFFFF000
_ZERO_DIGEST = bytes(48)
_PAGE_INFO_LEN = 0x70


class LaunchDigest:
    """The running SEV-SNP launch digest: ``ld' = SHA-384(PAGE_INFO(ld, page))`` per page.

    PAGE_INFO is 0x70 bytes: the current digest, the page's contents digest (zeros for
    pages the PSP populates itself), the length, the page type, the IMI flag, three VMPL
    permission bytes and the GPA. Pages must be folded in exactly the order QEMU hands them
    to SNP_LAUNCH_UPDATE.
    """

    def __init__(self) -> None:
        self._ld = _ZERO_DIGEST

    @property
    def digest(self) -> bytes:
        return self._ld

    def _update(self, gpa: int, page_type: int, contents: bytes) -> None:
        page_info = (
            self._ld
            + contents
            + struct.pack("<HBB", _PAGE_INFO_LEN, page_type, 0)  # len, type, IMI=0
            + bytes(4)  # VMPL3/2/1 permissions, reserved
            + struct.pack("<Q", gpa)
        )
        self._ld = hashlib.sha384(page_info).digest()

    def normal_pages(self, gpa: int, data: bytes) -> None:
        if len(data) % PAGE_SIZE:
            raise MeasurementError(
                f"normal pages must be page-aligned, got {len(data)} bytes"
            )
        for off in range(0, len(data), PAGE_SIZE):
            end = off + PAGE_SIZE
            self._update(
                gpa + off, PAGE_TYPE_NORMAL, hashlib.sha384(data[off:end]).digest()
            )

    def zero_pages(self, gpa: int, length: int) -> None:
        for off in range(0, length, PAGE_SIZE):
            self._update(gpa + off, PAGE_TYPE_ZERO, _ZERO_DIGEST)

    def secrets_page(self, gpa: int) -> None:
        self._update(gpa, PAGE_TYPE_SECRETS, _ZERO_DIGEST)

    def cpuid_page(self, gpa: int) -> None:
        self._update(gpa, PAGE_TYPE_CPUID, _ZERO_DIGEST)

    def vmsa_page(self, page: bytes) -> None:
        self._update(VMSA_GPA, PAGE_TYPE_VMSA, hashlib.sha384(page).digest())


# ── the firmware (OVMF reset-vector GUIDed table + SEV metadata) ───────────────────────────────

_FOOTER_GUID = uuid.UUID("96b582de-1fb2-45f7-baea-a366c55a082d")
_SEV_HASH_TABLE_RV_GUID = uuid.UUID("7255371f-3a3b-4b04-927b-1da6efa8d454")
_SEV_ES_RESET_BLOCK_GUID = uuid.UUID("00f771de-1a7e-4fcb-890e-68c77e2fb44e")
_SEV_METADATA_GUID = uuid.UUID("dc886566-984a-4798-a75e-5585a7bf67cc")

# SEV metadata section types (OvmfPkg/ResetVector/X64/OvmfSevMetadata.asm).
SECTION_SNP_SEC_MEM = 0x1
SECTION_SNP_SECRETS = 0x2
SECTION_CPUID = 0x3
SECTION_SVSM_CAA = 0x4
SECTION_SNP_KERNEL_HASHES = 0x10

_ENTRY_HEADER = struct.Struct("<H16s")  # size, GUID (little-endian bytes)


@dataclass(frozen=True)
class MetadataSection:
    gpa: int
    size: int
    kind: int


class OvmfImage:
    """An AmdSev OVMF image, parsed for the three things the digest needs.

    The firmware is mapped so it ends at 4 GiB. Its reset-vector GUIDed table (which ends 32
    bytes before the end of the image, and is read backwards) locates the SEV-ES AP reset
    address, the kernel-hashes table and the SEV metadata listing the pages to pre-populate.
    """

    def __init__(self, data: bytes) -> None:
        if not data or len(data) % PAGE_SIZE:
            raise MeasurementError(
                f"firmware size {len(data)} is not a whole number of pages"
            )
        self.data = data
        self._table = self._parse_footer_table()

    @classmethod
    def load(cls, path: str) -> "OvmfImage":
        if not os.path.isfile(path):
            raise MeasurementError(
                f"guest firmware {path} not found — the launch digest is a hash of these exact "
                "bytes, so the pinned firmware must be present (see firmware/PROVENANCE.md)"
            )
        return cls(Path(path).read_bytes())

    @property
    def gpa(self) -> int:
        return FOUR_GIB - len(self.data)

    def _parse_footer_table(self) -> dict[uuid.UUID, bytes]:
        footer_start = len(self.data) - 32 - _ENTRY_HEADER.size
        size, guid = _ENTRY_HEADER.unpack_from(self.data, footer_start)
        if uuid.UUID(bytes_le=guid) != _FOOTER_GUID:
            raise MeasurementError(
                "firmware has no OVMF reset-vector table footer — not an OVMF image"
            )
        table_start = footer_start - (size - _ENTRY_HEADER.size)
        table = self.data[table_start:footer_start]
        entries: dict[uuid.UUID, bytes] = {}
        # Entries are read from the end: each is <data><size u16><guid>, size covering all three.
        while len(table) >= _ENTRY_HEADER.size:
            header_start = len(table) - _ENTRY_HEADER.size
            size, guid = _ENTRY_HEADER.unpack_from(table, header_start)
            if size < _ENTRY_HEADER.size or size > len(table):
                raise MeasurementError("malformed OVMF reset-vector table entry")
            entry_start = len(table) - size
            entries[uuid.UUID(bytes_le=guid)] = table[entry_start:header_start]
            table = table[:entry_start]
        return entries

    def _entry(self, guid: uuid.UUID, what: str) -> bytes:
        try:
            return self._table[guid]
        except KeyError:
            raise MeasurementError(
                f"firmware has no {what} entry — it is not an SEV-SNP (AmdSevX64) build"
            )

    @property
    def sev_es_reset_eip(self) -> int:
        """Where APs start. The BSP always starts at the architectural reset vector."""
        return struct.unpack_from(
            "<I", self._entry(_SEV_ES_RESET_BLOCK_GUID, "SEV-ES reset")
        )[0]

    @property
    def kernel_hashes_gpa(self) -> int:
        entry = self._entry(_SEV_HASH_TABLE_RV_GUID, "kernel-hashes table")
        return struct.unpack_from("<I", entry)[0]

    @property
    def metadata_sections(self) -> list[MetadataSection]:
        offset = struct.unpack_from(
            "<I", self._entry(_SEV_METADATA_GUID, "SEV metadata")
        )[0]
        start = len(self.data) - offset
        signature, _size, _version, count = struct.unpack_from(
            "<4sIII", self.data, start
        )
        if signature != b"ASEV":
            raise MeasurementError(f"bad SEV metadata signature {signature!r}")
        return [
            MetadataSection(*struct.unpack_from("<III", self.data, start + 16 + 12 * i))
            for i in range(count)
        ]


# ── the kernel-hashes page (QEMU target/i386/sev.c, kernel-hashes=on) ──────────────────────────

_HASH_TABLE_HEADER_GUID = uuid.UUID("9438d606-4f22-4cc9-b479-a793d411fd21")
_KERNEL_ENTRY_GUID = uuid.UUID("4de79437-abd2-427f-b835-d5b172d2045b")
_INITRD_ENTRY_GUID = uuid.UUID("44baf731-3a2f-4bd7-9af1-41e29169781d")
_CMDLINE_ENTRY_GUID = uuid.UUID("97d02dd8-bd20-4c94-aa78-e7714d36ab2a")


def kernel_hashes_page(
    kernel_sha256: bytes, initrd_sha256: bytes, cmdline: str, offset: int
) -> bytes:
    """The page QEMU writes the SHA-256 hashes table into, as OVMF will verify it.

    Table: header GUID + length, then cmdline, initrd, kernel entries (GUID, length, SHA-256),
    zero-padded to 16 bytes. The cmdline is hashed with its terminating NUL, exactly as QEMU
    passes it to the kernel; ``offset`` is where in the page the firmware expects the table.
    Only the kernel's and initrd's digests enter the measurement, never their bytes.
    """

    def entry(guid: uuid.UUID, digest: bytes) -> bytes:
        return guid.bytes_le + struct.pack("<H", 16 + 2 + 32) + digest

    entries = (
        entry(_CMDLINE_ENTRY_GUID, hashlib.sha256(cmdline.encode() + b"\x00").digest())
        + entry(_INITRD_ENTRY_GUID, initrd_sha256)
        + entry(_KERNEL_ENTRY_GUID, kernel_sha256)
    )
    table = (
        _HASH_TABLE_HEADER_GUID.bytes_le
        + struct.pack("<H", 16 + 2 + len(entries))
        + entries
    )
    table += bytes(-len(table) % 16)
    if offset + len(table) > PAGE_SIZE:
        raise MeasurementError(
            f"kernel-hashes table at offset {offset:#x} overflows its page"
        )
    page = bytearray(PAGE_SIZE)
    end = offset + len(table)
    page[offset:end] = table
    return bytes(page)


# ── the VMSA (vCPU reset state KVM + QEMU establish; Linux struct sev_es_save_area) ─────────────

BSP_RESET_EIP = 0xFFFFFFF0
# SEV_FEATURES the VMSA carries. QEMU >= 9.1 initialises through KVM_SEV_INIT2 and requests no
# optional features, leaving only SNPActive. A host whose KVM/QEMU adds any (e.g. DebugSwap,
# bit 5) produces a different digest; the live-report tests catch that.
SEV_FEATURES_SNP_ACTIVE = 0x1


def _segment(selector: int, attrib: int, limit: int, base: int) -> bytes:
    return struct.pack("<HHIQ", selector, attrib, limit, base)


def vmsa_page(eip: int, vcpu_signature: int, sev_features: int) -> bytes:
    """One vCPU's initial VMSA as the PSP measures it: the reset state, zero elsewhere.

    Offsets follow ``struct sev_es_save_area``. Values are the x86 reset state as QEMU and KVM
    set it for an SEV-SNP vCPU: real mode at ``eip``, EFER.SVME, CR4.MCE, CR0.ET, the PAT
    default, the CPUID leaf-1 signature in RDX, and QEMU's FPU defaults (MXCSR, x87 FCW).
    """
    vmsa = bytearray(PAGE_SIZE)

    def put(offset: int, fmt: str, *values: int) -> None:
        struct.pack_into(fmt, vmsa, offset, *values)

    data_seg = _segment(0, 0x93, 0xFFFF, 0)
    segments = {
        0x000: data_seg,  # es
        0x010: _segment(0xF000, 0x9B, 0xFFFF, eip & 0xFFFF0000),  # cs
        0x020: data_seg,  # ss
        0x030: data_seg,  # ds
        0x040: data_seg,  # fs
        0x050: data_seg,  # gs
        0x060: _segment(0, 0, 0xFFFF, 0),  # gdtr
        0x070: _segment(0, 0x82, 0xFFFF, 0),  # ldtr
        0x080: _segment(0, 0, 0xFFFF, 0),  # idtr
        0x090: _segment(0, 0x8B, 0xFFFF, 0),  # tr
    }
    for offset, seg in segments.items():
        end = offset + len(seg)
        vmsa[offset:end] = seg

    put(0x0D0, "<Q", 0x1000)  # efer: SVME
    put(0x148, "<Q", 0x40)  # cr4: MCE
    put(0x158, "<Q", 0x10)  # cr0: ET
    put(0x160, "<Q", 0x400)  # dr7
    put(0x168, "<Q", 0xFFFF0FF0)  # dr6
    put(0x170, "<Q", 0x2)  # rflags
    put(0x178, "<Q", eip & 0xFFFF)  # rip
    put(0x268, "<Q", 0x0007040600070406)  # g_pat
    put(0x310, "<Q", vcpu_signature)  # rdx
    put(0x3B0, "<Q", sev_features)
    put(0x3E8, "<Q", 0x1)  # xcr0
    put(0x408, "<I", 0x1F80)  # mxcsr
    put(0x410, "<H", 0x37F)  # x87_fcw
    return bytes(vmsa)


# ── inputs from a host profile ─────────────────────────────────────────────────────────────────


def vcpu_signature(processor_id: str | None) -> int:
    """The vCPU's CPUID leaf-1 EAX from a fingerprint's ``cpu_processor_id``.

    ``processor_id`` packs leaf-1 EAX little-endian into its first four bytes (the EDX half is a
    TDX baseline constant and means nothing on AMD). QEMU loads that EAX into RDX at reset, so
    it is hashed into every VMSA: a measurement input as much as the firmware. A missing id is
    refused rather than defaulted to the generating host's CPU -- that fallback would silently
    produce a plausible measurement for the wrong machine.
    """
    if not processor_id:
        raise MeasurementError(
            "fingerprint carries no cpu_processor_id; the SEV-SNP launch measurement is bound "
            "to the vCPU signature, so generating without it would emit a measurement for the "
            "wrong CPU. Re-register a host of this class (`chutes-cvm host submit-profile`)."
        )
    try:
        raw = bytes.fromhex(processor_id)
    except ValueError:
        raise MeasurementError(f"malformed cpu_processor_id: {processor_id!r}")
    if len(raw) < 4:
        raise MeasurementError(f"cpu_processor_id too short: {processor_id!r}")
    return int.from_bytes(raw[:4], "little")


# ── the whole launch ───────────────────────────────────────────────────────────────────────────


def launch_digest(
    firmware: OvmfImage,
    *,
    vcpus: int,
    vcpu_signature: int,
    kernel_sha256: bytes,
    initrd_sha256: bytes,
    cmdline: str,
    sev_features: int = SEV_FEATURES_SNP_ACTIVE,
) -> bytes:
    """The SEV-SNP launch digest QEMU's launch sequence produces, in the same order.

    The kernel and initrd enter only through their SHA-256 digests (the hashes page), so
    they are passed as digests: a multi-hundred-MB initrd never needs to be held in memory.
    """
    if vcpus < 1:
        raise MeasurementError(f"vcpus must be positive, got {vcpus}")
    ld = LaunchDigest()
    ld.normal_pages(firmware.gpa, firmware.data)

    for section in firmware.metadata_sections:
        if section.kind in (SECTION_SNP_SEC_MEM, SECTION_SVSM_CAA):
            ld.zero_pages(section.gpa, section.size)
        elif section.kind == SECTION_SNP_SECRETS:
            ld.secrets_page(section.gpa)
        elif section.kind == SECTION_CPUID:
            ld.cpuid_page(section.gpa)
        elif section.kind == SECTION_SNP_KERNEL_HASHES:
            hashes_gpa = firmware.kernel_hashes_gpa
            if hashes_gpa & ~(PAGE_SIZE - 1) != section.gpa:
                raise MeasurementError(
                    f"kernel-hashes table {hashes_gpa:#x} is outside its metadata section "
                    f"{section.gpa:#x}"
                )
            page = kernel_hashes_page(
                kernel_sha256, initrd_sha256, cmdline, hashes_gpa & (PAGE_SIZE - 1)
            )
            ld.normal_pages(section.gpa, page)
        else:
            raise MeasurementError(
                f"unknown SEV metadata section type {section.kind:#x}"
            )

    bsp = vmsa_page(BSP_RESET_EIP, vcpu_signature, sev_features)
    ap = vmsa_page(firmware.sev_es_reset_eip, vcpu_signature, sev_features)
    ld.vmsa_page(bsp)
    for _ in range(vcpus - 1):
        ld.vmsa_page(ap)
    return ld.digest


def _sha256_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").digest()


@dataclass(frozen=True)
class SnpImage:
    """The release half of the SEV-SNP launch digest: firmware and direct-boot artifacts.

    Every AMD class of a release shares these; the vCPU count, signature and ACPI hash differ
    per class. Loaded once per release -- the initrd alone is ~90 MB -- and loading is where a
    missing input surfaces, so it fails the release rather than any one class.
    """

    firmware: OvmfImage
    kernel_sha256: bytes
    initrd_sha256: bytes
    cmdline: str

    @classmethod
    def load(cls, image: str, firmware: str) -> "SnpImage":
        kernel, initrd, cmdline = staged_boot_artifacts(image)
        return cls(
            firmware=OvmfImage.load(firmware),
            kernel_sha256=_sha256_file(kernel),
            initrd_sha256=_sha256_file(initrd),
            cmdline=cmdline,
        )

    def measurement(
        self, vcpus: int, processor_id: str | None, acpi_sha256: str
    ) -> str:
        """This image's launch digest on one hardware class, as a measurements.yaml value.

        ``acpi_sha256`` goes on the cmdline exactly as an SEV-SNP launch appends it
        (``SnpLaunchContext.with_acpi``), and must be
        a real hash: a published measurement computed with the test-boot sentinel would let a
        host boot unchecked ACPI and still attest. Bare uppercase hex (96 chars), the same width
        and shape as an RTMR.
        """
        if not _SHA256_HEX.fullmatch(acpi_sha256):
            raise MeasurementError(
                f"an SEV-SNP measurement needs a real ACPI hash, got {acpi_sha256!r}"
            )
        digest = launch_digest(
            self.firmware,
            vcpus=vcpus,
            vcpu_signature=vcpu_signature(processor_id),
            kernel_sha256=self.kernel_sha256,
            initrd_sha256=self.initrd_sha256,
            cmdline=SnpLaunchContext.with_acpi(self.cmdline, acpi_sha256),
        )
        return digest.hex().upper()


# ── ACPI (checked by the firmware, attested through the cmdline) ──────────────────────────────

# The launch digest does not cover ACPI, so the AmdSev firmware verifies the tables itself: it
# hashes ``etc/table-loader`` and every blob that script allocates -- the same inputs TDX measures
# into RTMR0 -- and refuses to install them unless the result equals ``sek8s.acpi_sha256`` on the
# kernel cmdline, which the digest does cover. The framing must match the firmware's
# (firmware/patches/amdsev/03-acpi.patch): each blob is ``name[56, NUL-padded] || size (u64 LE)
# || bytes``, the loader first, then every ALLOCATE'd blob in loader order.

ACPI_TABLES_FILE = "etc/acpi/tables"
ACPI_RSDP_FILE = "etc/acpi/rsdp"
ACPI_LOADER_FILE = "etc/table-loader"

# QEMU's BIOS linker-loader ABI (hw/acpi/bios-linker-loader.c).
_FNAME_SIZE = 56
_ENTRY_SIZE = 128
_LOADER_SIZE = 4096
_CMD_ALLOCATE, _CMD_ADD_POINTER, _CMD_ADD_CHECKSUM = 1, 2, 3
_ZONE_HIGH, _ZONE_FSEG = 1, 2
_RSDT_HEADER = 36


def _fname(name: str) -> bytes:
    raw = name.encode()
    if len(raw) >= _FNAME_SIZE:
        raise MeasurementError(f"fw_cfg file name too long: {name!r}")
    return raw.ljust(_FNAME_SIZE, b"\0")


def _allocate(file: str, alignment: int, zone: int) -> bytes:
    body = _fname(file) + struct.pack("<IB", alignment, zone)
    return struct.pack("<I", _CMD_ALLOCATE) + body.ljust(_ENTRY_SIZE - 4, b"\0")


def _add_pointer(pointer_file: str, pointee_file: str, offset: int, size: int) -> bytes:
    body = (
        _fname(pointer_file) + _fname(pointee_file) + struct.pack("<IB", offset, size)
    )
    return struct.pack("<I", _CMD_ADD_POINTER) + body.ljust(_ENTRY_SIZE - 4, b"\0")


def _add_checksum(file: str, result_offset: int, start: int, length: int) -> bytes:
    body = _fname(file) + struct.pack("<III", result_offset, start, length)
    return struct.pack("<I", _CMD_ADD_CHECKSUM) + body.ljust(_ENTRY_SIZE - 4, b"\0")


@dataclass(frozen=True)
class AcpiTable:
    """One table's place in ``etc/acpi/tables``."""

    signature: str
    offset: int
    length: int


@dataclass(frozen=True)
class AcpiTables:
    """A host class's ``etc/acpi/tables``, and the ACPI hash its SEV-SNP firmware expects.

    Only the tables come from the dump; ``etc/table-loader`` and ``etc/acpi/rsdp`` are QEMU's
    deterministic functions of them, rebuilt here the way the tdx-measure fork rebuilds them for
    its RTMR0 events. All three were checked byte for byte against a live 8-GPU SEV-SNP guest.
    """

    tables: bytes

    @classmethod
    def dump(
        cls, host: HostProfile, *, firmware: str, tdx_measure_bin: str, dist: str
    ) -> "AcpiTables":
        """Dump ``host``'s tables offline: the dump RTMR0 already relies on, run on the class's
        measurement command (``MeasurementContext.from_host``).

        TODO: this runs tdx-measure's full ``--create-acpi-tables`` pass, which also computes MRTD
        and RTMR0 -- TDX values an SEV-SNP class discards, computed over the AMD firmware. Cheap
        today; replace with a dump-only mode in the tdx-measure fork so SNP generation runs no TDX
        code.
        """
        cmd = QemuCommand.build(
            host, MeasurementContext.from_host(host, firmware=firmware)
        )
        with tempfile.TemporaryDirectory() as td:
            tables_path = Path(td) / "acpi_tables.bin"
            meta = ImageConfig(cmd, host, acpi_tables=str(tables_path)).to_dict()
            tdx.generate_acpi_blobs(
                meta, Path(td), tdx_measure_bin=tdx_measure_bin, dist=dist
            )
            if not tables_path.is_file():
                raise MeasurementError(
                    f"ACPI dump wrote no tables for {host.variant_label}"
                )
            return cls(tables_path.read_bytes())

    def listed(self) -> list[AcpiTable]:
        """Each table in file order, up to the zero padding after the last."""
        found, off = [], 0
        while off + 8 <= len(self.tables):
            sig_end = off + 4
            sig = self.tables[off:sig_end]
            if not all(32 <= c < 127 for c in sig):
                break
            (length,) = struct.unpack_from("<I", self.tables, off + 4)
            if length < 8 or off + length > len(self.tables):
                raise MeasurementError(
                    f"ACPI table at {off:#x} has invalid length {length}"
                )
            found.append(AcpiTable(sig.decode(), off, length))
            off += length
        return found

    def _table(self, signature: str) -> AcpiTable:
        for table in self.listed():
            if table.signature == signature:
                return table
        raise MeasurementError(f"ACPI dump has no {signature} table")

    @property
    def loader(self) -> bytes:
        """``etc/table-loader`` as QEMU serves it for these tables.

        The command order is QEMU's ``acpi_build``: allocate RSDP and tables, checksum DSDT, the
        FADT's FACS/DSDT/X_DSDT pointers and checksum, every other table's checksum in file order,
        one pointer per RSDT entry, RSDT's checksum, then the RSDP's pointer and checksum.
        """
        dsdt, facp, rsdt = self._table("DSDT"), self._table("FACP"), self._table("RSDT")
        if (rsdt.length - _RSDT_HEADER) % 4:
            raise MeasurementError(f"malformed RSDT length {rsdt.length}")
        tables = ACPI_TABLES_FILE
        cmds = [
            _allocate(ACPI_RSDP_FILE, 16, _ZONE_FSEG),
            _allocate(tables, 64, _ZONE_HIGH),
            _add_checksum(tables, dsdt.offset + 9, dsdt.offset, dsdt.length),
            _add_pointer(tables, tables, facp.offset + 36, 4),  # FIRMWARE_CTRL -> FACS
            _add_pointer(tables, tables, facp.offset + 40, 4),  # DSDT
            _add_pointer(tables, tables, facp.offset + 140, 8),  # X_DSDT
            _add_checksum(tables, facp.offset + 9, facp.offset, facp.length),
        ]
        # FACS has no checksum slot; DSDT, FACP and RSDT are handled on their own.
        for t in self.listed():
            if t.signature not in ("FACS", "DSDT", "FACP", "RSDT"):
                cmds.append(_add_checksum(tables, t.offset + 9, t.offset, t.length))
        for i in range((rsdt.length - _RSDT_HEADER) // 4):
            entry = rsdt.offset + _RSDT_HEADER + 4 * i
            cmds.append(_add_pointer(tables, tables, entry, 4))
        cmds += [
            _add_checksum(tables, rsdt.offset + 9, rsdt.offset, rsdt.length),
            _add_pointer(ACPI_RSDP_FILE, tables, 16, 4),
            _add_checksum(ACPI_RSDP_FILE, 8, 0, 20),
        ]
        loader = b"".join(cmds)
        if len(loader) > _LOADER_SIZE:
            raise MeasurementError(f"table-loader overflows {_LOADER_SIZE} bytes")
        return loader.ljust(_LOADER_SIZE, b"\0")

    @property
    def rsdp(self) -> bytes:
        """``etc/acpi/rsdp``: ACPI 1.0, its RSDT address an offset into ``etc/acpi/tables`` until
        the firmware applies the loader's pointer; the checksum is left for the firmware.
        """
        rsdt = self._table("RSDT")
        return b"RSD PTR " + b"\0" + b"BOCHS " + b"\0" + struct.pack("<I", rsdt.offset)

    def sha256(self) -> str:
        """The value the firmware checks these tables against (``sek8s.acpi_sha256``)."""
        return self.framed_sha256(
            self.loader, {ACPI_TABLES_FILE: self.tables, ACPI_RSDP_FILE: self.rsdp}
        )

    @staticmethod
    def framed_sha256(loader: bytes, blobs: Mapping[str, bytes]) -> str:
        """The firmware's hash over ``loader`` and the fw_cfg ``blobs`` it allocates, framed as
        the firmware frames them."""
        if len(loader) % _ENTRY_SIZE:
            raise MeasurementError("etc/table-loader is not a whole number of entries")
        h = hashlib.sha256()

        def item(name: str, data: bytes) -> None:
            h.update(_fname(name) + struct.pack("<Q", len(data)) + data)

        item(ACPI_LOADER_FILE, loader)
        for off in range(0, len(loader), _ENTRY_SIZE):
            if struct.unpack_from("<I", loader, off)[0] != _CMD_ALLOCATE:
                continue
            name_start = off + 4
            name_end = name_start + _FNAME_SIZE
            name = loader[name_start:name_end].split(b"\0", 1)[0].decode()
            if name not in blobs:
                raise MeasurementError(
                    f"table-loader allocates {name!r}, which was not supplied"
                )
            item(name, blobs[name])
        return h.hexdigest()


# ── the platform ───────────────────────────────────────────────────────────────────────────────


class SnpMeasurements(PlatformMeasurements):
    """AMD SEV-SNP: one launch digest per class, over inputs the whole release shares.

    The firmware and the image's direct-boot artifacts are loaded once; each class adds its
    vCPU count and signature, and its ACPI hash from the same offline dump RTMR0 uses. Classes
    whose ACPI tables agree (RAM size, for one, does not change them) share a measurement.
    """

    key = "snp"
    provider = SnpTeeProvider

    def __init__(
        self, *, bios_dir: str, image: str, tdx_measure_bin: str, dist: str
    ) -> None:
        super().__init__()
        self._firmware = str(Path(bios_dir) / SnpTeeProvider.default_firmware)
        self._image = SnpImage.load(image, self._firmware)
        self._tdx_measure_bin = tdx_measure_bin
        self._dist = dist

    def measure(self, host: HostProfile) -> dict:
        # The ACPI value comes first: the dump does not depend on it (the tables are built from
        # the machine shape, not the cmdline), and the digest does.
        acpi_sha256 = AcpiTables.dump(
            host,
            firmware=self._firmware,
            tdx_measure_bin=self._tdx_measure_bin,
            dist=self._dist,
        ).sha256()
        return {
            "measurement": self._image.measurement(
                host.vcpus, host.cpu.processor_id, acpi_sha256
            ),
            # What an SEV-SNP launch of this class puts on its cmdline.
            "acpi_sha256": acpi_sha256,
        }

    def section(self) -> dict:
        # No version-level values: firmware and kernel/initrd/cmdline are already folded into
        # each hardware entry's launch digest.
        return {"hardware": self.hardware}
