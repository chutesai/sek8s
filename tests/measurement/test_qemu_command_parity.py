"""The RTMR0-shaping topology, which one traversal now produces for both purposes.

There is no second assembly left to compare against: ``LaunchCommandBuilder`` and
``MeasurementCommandBuilder`` share ``QemuCommandBuilder``'s walk, so root ports, PXB bridges,
chassis numbering and slot allocation cannot differ between a launch and a measurement by
construction. What is worth pinning is the shape that walk produces, and that the endpoint hung
off each root port is the right one for each purpose -- a ``vfio-pci`` for a launch, a
``pci-bar-stub`` carrying that device's BARs for the dump.
"""

import topology_fixtures as known
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.qemu import (
    LaunchCommandBuilder,
    MeasurementCommandBuilder,
    PassthroughSet,
    PcieRootPinning,
    QemuCommand,
)

_FW = "OVMF.inteltdx.fd"


def _synth(doc):
    """The measurement command, built from a HostProfile over the captured device lists."""
    return QemuCommand.for_measurement(HostProfile(doc), firmware=_FW).to_args()


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
    call the same traversal, fed from the same captured devices.
    What is worth pinning is the shape it produces.
    """
    args = _topology_args(_synth(known.rtx_numa_doc()))
    assert _slots(args, "pxb-pcie") == ["0x18", "0x19"]  # one per node, 24 + node
    assert [
        a.split("id=")[1].split(",")[0] for a in args if a.startswith("pcie-root-port")
    ] == [f"rp{i}" for i in range(1, 9)]
    # The dump hangs a stub off each root port, built from that device's own captured geometry --
    # no BDF, because the generating box has no such device on its bus. A launch hangs the real
    # vfio-pci endpoint off the very same root port.
    host = HostProfile(known.rtx_numa_doc())
    assert any(
        "pci-bar-stub" in d and "bus=rp1" in d
        for d in QemuCommand.for_measurement(host, firmware=_FW).devices
    )
    assert (
        LaunchCommandBuilder(host).endpoint(host.gpus[0], "rp1")
        == f"vfio-pci,host={host.gpus[0].bdf},bus=rp1,addr=0x0,iommufd=iommufd0"
    )


def test_uneven_numa_split_follows_the_captured_vector():
    """Root ports hang off the bridge for each device's own node, so a 3/5 split is not 4/4."""
    nodes = (0, 0, 0, 1, 1, 1, 1, 1)
    args = _topology_args(
        _synth(known.host_document("RTX_PRO_6000", vcpus=124, gpu_nodes=nodes))
    )
    buses = [
        a.split("bus=")[1].split(",")[0] for a in args if a.startswith("pcie-root-port")
    ]
    assert buses == ["pxb_numa0"] * 3 + ["pxb_numa1"] * 5


def test_flat_topology_has_no_pxb():
    args = _topology_args(_synth(known.rtx_flat_doc()))
    assert not any("pxb-pcie" in a for a in args)
    assert _slots(args, "pcie-root-port")[0] == "0x8"


def test_nvswitch_endpoints_follow_the_gpus():
    """NVSwitch root ports are numbered after the GPUs and keep their own node placement."""
    args = _topology_args(_synth(known.h200_doc(nvswitch_node=1)))
    ids = [
        a.split("id=")[1].split(",")[0] for a in args if a.startswith("pcie-root-port")
    ]
    assert ids == [f"rp{i}" for i in range(1, 9)] + [f"rp_nvsw{i}" for i in range(1, 5)]


def test_cpu_args_come_from_the_reported_qemu_version():
    doc = known.rtx_numa_doc()
    doc["qemu"]["qemu_version"] = "10.2.1"
    assert HostProfile(doc).cpu_args == "host,-avx10"
    # Unknown/unsupported QEMU versions take the same -avx10 form.
    doc["qemu"]["qemu_version"] = "99.9.9"
    assert HostProfile(doc).cpu_args == "host,-avx10"


def test_pci_topology_takes_the_decision_it_is_given():
    """The PCI topology must agree with the memory topology: PXB bridges name guest NUMA nodes,
    so building them for a guest with no `-numa` is not a valid command -- QEMU refuses with
    "Illegal numa node 0".

    This used to be re-derived from the live host instead of taken from the caller, so a
    launcher that chose flat still got PXB bridges.
    """
    host = HostProfile(known.h200_doc())
    passthrough = PassthroughSet.from_profile(host)

    def topology(uses_guest_numa: bool) -> list[str]:
        """The topology the traversal emits for a profile that says flat or NUMA.

        ``_passthrough`` reads only ``uses_guest_numa`` off the profile -- which devices reach the
        guest is the PassthroughSet's business -- so a stub states the one decision directly.
        """
        cmd = QemuCommand.for_measurement(host, firmware=_FW)
        cmd.devices = []
        builder = MeasurementCommandBuilder(
            known.QemuProfileStub(
                mem="8G",
                smp_topology="4,sockets=1,cores=4,threads=1",
                uses_guest_numa=uses_guest_numa,
                tee_provider=host.tee_provider,
            )
        )
        builder._passthrough(cmd, passthrough, PcieRootPinning(uses_guest_numa))
        return cmd.devices

    assert not any("pxb-pcie" in d for d in topology(False))
    assert any("pxb-pcie" in d for d in topology(True))
