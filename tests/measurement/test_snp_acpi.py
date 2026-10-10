"""The expected-ACPI hash an SEV-SNP class boots with (``measurement.snp.AcpiTables``).

The fixtures are the fw_cfg blobs QEMU served a live SEV-SNP guest (EPYC 7763, 8x RTX PRO 6000,
the class's full measurement shape: tap NIC, config/cache/storage drives), read back from
``/sys/firmware/qemu_fw_cfg`` inside the guest. ``LIVE_ACPI_SHA256`` is the value the patched
firmware printed for those same tables on that boot, so these tests pin the offline derivation
to what the firmware actually computes, not to a second copy of its algorithm.
"""

import gzip
import hashlib
import struct
from pathlib import Path
from unittest.mock import patch

import pytest
import topology_fixtures as tf
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.measurement import tdx
from chutes_cvm.measurement.platform import MeasurementError
from chutes_cvm.measurement.snp import (
    ACPI_LOADER_FILE,
    ACPI_RSDP_FILE,
    ACPI_TABLES_FILE,
    AcpiTables,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "acpi" / "rtx8-snp"
LIVE_ACPI_SHA256 = "6a8501a0f92db861ac1f055a4015adc52662da7b760463ec0331e40bb1f5f5a7"


def _blob(name: str) -> bytes:
    return gzip.decompress((_FIXTURES / f"{name.replace('/', '_')}.gz").read_bytes())


@pytest.fixture(scope="module")
def live():
    return {
        name: _blob(name)
        for name in (ACPI_LOADER_FILE, ACPI_TABLES_FILE, ACPI_RSDP_FILE)
    }


def test_the_loader_and_rsdp_are_rebuilt_exactly_as_qemu_served_them(live):
    tables = AcpiTables(live[ACPI_TABLES_FILE])
    assert tables.loader == live[ACPI_LOADER_FILE]
    assert tables.rsdp == live[ACPI_RSDP_FILE]


def test_the_offline_value_is_the_one_the_firmware_computed(live):
    assert AcpiTables(live[ACPI_TABLES_FILE]).sha256() == LIVE_ACPI_SHA256


def test_the_framing_binds_every_blob(live):
    """Changing any byte of any hashed blob moves the value, as it must in the firmware."""
    loader = live[ACPI_LOADER_FILE]
    blobs = {
        ACPI_TABLES_FILE: live[ACPI_TABLES_FILE],
        ACPI_RSDP_FILE: live[ACPI_RSDP_FILE],
    }
    assert AcpiTables.framed_sha256(loader, blobs) == LIVE_ACPI_SHA256

    for name in blobs:
        tampered = dict(blobs)
        tampered[name] = bytes([blobs[name][0] ^ 1]) + blobs[name][1:]
        assert AcpiTables.framed_sha256(loader, tampered) != LIVE_ACPI_SHA256
    assert AcpiTables.framed_sha256(loader[:-1] + b"\1", blobs) != LIVE_ACPI_SHA256


def test_the_framing_is_the_firmware_s(live):
    """name[56, NUL-padded] || u64 LE size || bytes, loader first, then each ALLOCATE in order."""
    loader, blobs = live[ACPI_LOADER_FILE], live

    def item(name, data):
        return name.encode().ljust(56, b"\0") + struct.pack("<Q", len(data)) + data

    stream = item(ACPI_LOADER_FILE, loader)
    stream += item(
        ACPI_RSDP_FILE, blobs[ACPI_RSDP_FILE]
    )  # first ALLOCATE in QEMU's script
    stream += item(ACPI_TABLES_FILE, blobs[ACPI_TABLES_FILE])
    assert hashlib.sha256(stream).hexdigest() == LIVE_ACPI_SHA256


def test_a_blob_the_loader_allocates_must_be_supplied(live):
    with pytest.raises(MeasurementError, match="allocates"):
        AcpiTables.framed_sha256(
            live[ACPI_LOADER_FILE], {ACPI_TABLES_FILE: live[ACPI_TABLES_FILE]}
        )


def test_tables_without_the_required_entries_are_refused(live):
    with pytest.raises(MeasurementError, match="no DSDT"):
        AcpiTables(bytes(4096)).loader


def _amd_host():
    return HostProfile.from_api_profile(
        tf.host_document(
            "RTX_PRO_6000",
            vcpus=124,
            gpu_nodes=(0,) * 8,
            cpu_vendor="AuthenticAMD",
            cpu_processor_id="110fa000fffba91f",
        )
    )


def test_a_class_is_dumped_with_its_own_platform_s_machine_and_hashed(live):
    """``AcpiTables.dump`` runs the RTMR0 dump on the class's measurement command -- whose
    machine follows the platform -- and the tables it writes hash to the firmware's value.
    """
    seen = {}

    def fake_dump(meta, out_dir, *, tdx_measure_bin, dist):
        seen["meta"], seen["bin"], seen["dist"] = meta, tdx_measure_bin, dist
        Path(meta["boot_config"]["acpi_tables"]).write_bytes(live[ACPI_TABLES_FILE])
        return {}

    with patch.object(tdx, "generate_acpi_blobs", side_effect=fake_dump):
        got = AcpiTables.dump(
            _amd_host(), firmware="/fw/OVMF.amdsev.fd", tdx_measure_bin="tm", dist="d"
        ).sha256()

    assert got == LIVE_ACPI_SHA256
    assert (seen["bin"], seen["dist"]) == ("tm", "d")
    assert seen["meta"]["boot_config"]["qemu"]["machine"].startswith(
        "q35,kernel_irqchip=split,vmport=off,smm=off"
    )


def test_a_dump_that_writes_no_tables_fails_the_class():
    with patch.object(tdx, "generate_acpi_blobs", return_value={}):
        with pytest.raises(MeasurementError, match="no tables"):
            AcpiTables.dump(
                _amd_host(),
                firmware="/fw/OVMF.amdsev.fd",
                tdx_measure_bin="tm",
                dist="d",
            )
