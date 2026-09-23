"""The measurement-golden cases, shared by the check and the regenerator.

Not a test module. It exists so the thing that WRITES a golden and the thing that CHECKS one
compute it through the same function -- a generator with its own copy of this logic can drift
from the assertion, and a golden produced by a slightly different code path is worse than none.

``snapshot`` pins the SEV C-bit parameters itself rather than relying on a pytest fixture, so it
is deterministic wherever it runs: read live, they come from /dev/cpu/0/cpuid and an AMD golden
generated on one machine would not match another.
"""

import copy
from pathlib import Path
from unittest.mock import patch

import topology_fixtures as known
from chutes_cvm.guest import tee as tee_module
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.measurement.image_config import ImageConfig

GOLDEN_DIR = Path(__file__).parent / "golden"
FIRMWARE = "/opt/ovmf/OVMF.fd"
CPU_ARGS = "host,-avx10"


def _amd(doc):
    """The same class on AMD silicon: one GPU profile, two TEEs."""
    doc = copy.deepcopy(doc)
    doc["cpu"]["vendor"] = "AuthenticAMD"
    doc["cpu"]["processor_id"] = "110fa100fffba91f"  # EPYC 9124 Genoa, verified
    return doc


CASES = {
    "rtx_numa_intel": known.rtx_numa_doc,
    "rtx_flat_intel": known.rtx_flat_doc,
    "h200_intel": known.h200_doc,
    "h200_nvswitch_node1_intel": lambda: known.h200_doc(nvswitch_node=1),
    "rtx_numa_amd": lambda: _amd(known.rtx_numa_doc()),
    "rtx_flat_amd": lambda: _amd(known.rtx_flat_doc()),
}


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def snapshot(name: str) -> dict:
    """The measurement inputs for one case: the command's args and the fork's metadata."""
    with patch.object(
        tee_module,
        "sev_cbit_parameters",
        lambda: (tee_module.DEFAULT_CBITPOS, tee_module.DEFAULT_REDUCED_PHYS_BITS),
    ):
        host = HostProfile(CASES[name]())
        cmd = host.qemu_command(
            firmware=FIRMWARE, cpu_args=CPU_ARGS, process_name="chutes-measure"
        )
        return {
            "args": cmd.to_args(),
            "metadata": ImageConfig(cmd, host, acpi_tables="/out/acpi.bin").to_dict(),
        }
