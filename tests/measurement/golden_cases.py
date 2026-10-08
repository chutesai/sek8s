"""The measurement-golden cases, shared by the check and the regenerator.

Not a test module. It exists so the thing that WRITES a golden and the thing that CHECKS one
compute it through the same function -- a generator with its own copy of this logic can drift
from the assertion, and a golden produced by a slightly different code path is worse than none.
"""

import copy
from pathlib import Path

import topology_fixtures as known
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.context import (
    DirectBoot,
    GuestNetwork,
    GuestVolumes,
    PassthroughSet,
    ProcessBundle,
)
from chutes_cvm.measurement.image_config import ImageConfig

GOLDEN_DIR = Path(__file__).parent / "golden"
FIRMWARE = "/opt/ovmf/OVMF.fd"


def _amd(doc):
    """The same class on AMD silicon: one GPU profile, two TEEs."""
    doc = copy.deepcopy(doc)
    doc["cpu"]["vendor"] = "AuthenticAMD"
    doc["cpu"]["processor_id"] = "110fa100fffba91f"  # EPYC 9124 Genoa, verified
    return doc


def _b300_doc():
    return known.host_document("B300", vcpus=124, gpu_nodes=(0,) * 4 + (1,) * 4)


CASES = {
    "rtx_numa_intel": known.rtx_numa_doc,
    "rtx_flat_intel": known.rtx_flat_doc,
    "h200_intel": known.h200_doc,
    "h200_nvswitch_node1_intel": lambda: known.h200_doc(nvswitch_node=1),
    "b300_intel": _b300_doc,
    "rtx_numa_amd": lambda: _amd(known.rtx_numa_doc()),
    "rtx_flat_amd": lambda: _amd(known.rtx_flat_doc()),
    "b300_amd": lambda: _amd(_b300_doc()),
    # g3-h100-small-dal-1 as captured: one socket, one NUMA node, one H100 PCIe.
    "h100_pcie_amd": lambda: known.host_document(
        "H100_PCIE",
        vcpus=28,
        gpu_nodes=(0,),
        sockets=1,
        cpu_vendor="AuthenticAMD",
        cpu_processor_id="110fa100fffba91f",
        host_mem_gb=187,
        numa_node_count=1,
    ),
}

#: A launch's resolved inputs. Fixed so the snapshot is about the command, not the paths.
LAUNCH_INPUTS = dict(
    img_path="/var/lib/chutes/vm.qcow2",
    boot=DirectBoot(kernel="/k", initrd="/i", cmdline="root=UUID=x ro"),
    net=GuestNetwork(network_type="tap", net_iface="tap0", ssh_port=2222),
    volumes=GuestVolumes(config="/c.qcow2", cache="/k.raw", storage="/s.raw"),
    process=ProcessBundle(name="chutes-td", pidfile="/p", logfile="/l"),
)


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def snapshot(name: str) -> dict:
    """Everything a refactor must not move, for one hardware class.

    ``metadata`` is the COMPLETE input to the tdx-measure fork, so it byte-determines MRTD and
    RTMR0. ``launch_args`` is what a real guest boots with, which determines the RTMR0 a live
    host actually attests -- it is snapshotted because the measurement half alone left it
    unlocked, and a refactor reordered a device option there unnoticed.
    """
    host = HostProfile.from_dict(CASES[name]())
    measure = known.measurement_command(host, firmware=FIRMWARE)
    launch = known.launch_command(
        host,
        firmware=FIRMWARE,
        host_nodes=[0, 1] if host.uses_guest_numa else [],
        passthrough=PassthroughSet.from_profile(host),
        **LAUNCH_INPUTS,
    )
    return {
        "launch_args": launch.to_args(),
        "measure_args": measure.to_args(),
        "metadata": ImageConfig(measure, host, acpi_tables="/out/acpi.bin").to_dict(),
    }
