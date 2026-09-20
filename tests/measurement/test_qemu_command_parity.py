"""The measurement spec must reproduce the live launcher's RTMR0-shaping args.

HostProfile.qemu_command must match, byte-for-byte, what the real launch path
(build_base_cmd + passthrough._build_pci_topology) emits with its sysfs lookups
mocked to the same NUMA layout — so offline measurements can't drift from a real
launch. Both paths emit a vfio-pci endpoint per root port; the measurement path
uses a placeholder BDF (later swapped for a pci-bar-stub), so the endpoint lines
are dropped from the comparison — only their BDF differs, and neither the BDF nor
the endpoint device type is settled here.
"""


import topology_fixtures as known
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.qemu import (
    build_pci_topology,
    cpu_args_for_qemu_version,
)

_FW = "OVMF.inteltdx.fd"


def _synth(doc):
    """The measurement command, built from a HostProfile over the captured device lists."""
    return (
        HostProfile(doc)
        .qemu_command(
            firmware=_FW, cpu_args="host,-avx10", process_name="chutes-measure"
        )
        .to_args()
    )


def _topology_args(cmd):
    """RTMR0-shaping args only: drop the `-device vfio-pci,...` endpoint pairs
    (both paths emit them; only the BDF differs, and the endpoint is swapped for a
    pci-bar-stub in measurement generation)."""
    out = []
    i = 0
    while i < len(cmd):
        if (
            cmd[i] == "-device"
            and i + 1 < len(cmd)
            and cmd[i + 1].startswith("vfio-pci")
        ):
            i += 2
            continue
        # Boot chain (RTMR1/2), not RTMR0-shaping: the launcher passes the real
        # kernel/initrd/cmdline, the measurement path placeholders — drop both.
        if cmd[i] in ("-kernel", "-initrd", "-append"):
            i += 2
            continue
        out.append(cmd[i])
        i += 1
    return out


def _slots(args, prefix):
    """The pcie.0 addresses of args starting with ``prefix``, in emission order."""
    return [a.split("addr=")[1].split(",")[0] for a in args if a.startswith(prefix)]


def test_numa_topology_groups_gpus_by_node():
    """One PXB per host NUMA node, each GPU on the bridge for its own node.

    There is no second assembly to compare against any more -- launch and measurement generation
    call the same builder (passthrough._build_pci_topology), fed from the same captured devices.
    What is worth pinning is the shape it produces.
    """
    args = _topology_args(_synth(known.rtx_numa_doc()))
    assert _slots(args, "pxb-pcie") == ["0x18", "0x19"]  # one per node, 24 + node
    assert [a.split("id=")[1].split(",")[0] for a in args if a.startswith("pcie-root-port")] == [
        f"rp{i}" for i in range(1, 9)
    ]
    # The command is native, so endpoints carry the captured devices' real BDFs; image_config
    # swaps each for a pci-bar-stub, which is why no placeholder is invented here.
    assert any("vfio-pci,host=0000:19:00.0" in a for a in _synth(known.rtx_numa_doc()))


def test_uneven_numa_split_follows_the_captured_vector():
    """Root ports hang off the bridge for each device's own node, so a 3/5 split is not 4/4."""
    nodes = (0, 0, 0, 1, 1, 1, 1, 1)
    args = _topology_args(
        _synth(known.host_document("RTX_PRO_6000", vcpus=124, gpu_nodes=nodes))
    )
    buses = [a.split("bus=")[1].split(",")[0] for a in args if a.startswith("pcie-root-port")]
    assert buses == ["pxb_numa0"] * 3 + ["pxb_numa1"] * 5


def test_flat_topology_has_no_pxb():
    args = _topology_args(_synth(known.rtx_flat_doc()))
    assert not any("pxb-pcie" in a for a in args)
    assert _slots(args, "pcie-root-port")[0] == "0x8"


def test_nvswitch_endpoints_follow_the_gpus():
    """NVSwitch root ports are numbered after the GPUs and keep their own node placement."""
    args = _topology_args(_synth(known.h200_doc(nvswitch_node=1)))
    ids = [a.split("id=")[1].split(",")[0] for a in args if a.startswith("pcie-root-port")]
    assert ids == [f"rp{i}" for i in range(1, 9)] + [f"rp_nvsw{i}" for i in range(1, 5)]


def test_cpu_args_for_qemu_version():
    assert cpu_args_for_qemu_version("10.2.1") == "host,-avx10"
    # Unknown/unsupported QEMU versions fall back to the same -avx10 form.
    assert cpu_args_for_qemu_version("99.9.9") == "host,-avx10"


def test_pci_topology_takes_the_decision_it_is_given():
    """The PCI topology must agree with the memory topology: PXB bridges name guest NUMA nodes,
    so building them for a guest with no `-numa` is not a valid command -- QEMU refuses with
    "Illegal numa node 0".

    This used to be re-derived from the live host instead of taken from the caller, so a
    launcher that chose flat still got PXB bridges.
    """
    host = HostProfile(known.h200_doc())
    devices = dict(
        gpus=host.gpus, nvswitches=host.attached_nvswitches, ib_devices=host.attached_ib
    )

    flat = host.qemu_command(firmware=_FW, cpu_args="host,-avx10")
    flat.devices = []
    build_pci_topology(flat, **devices, guest_numa=False)
    assert not any("pxb-pcie" in d for d in flat.devices)

    numa = host.qemu_command(firmware=_FW, cpu_args="host,-avx10")
    numa.devices = []
    build_pci_topology(numa, **devices, guest_numa=True)
    assert any("pxb-pcie" in d for d in numa.devices)
