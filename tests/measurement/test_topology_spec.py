"""The measurement spec must reproduce the live launcher's RTMR0-shaping args.

build_topology_spec (fingerprint -> MachineSpec) fed through the shared
build_qemu_command must match, byte-for-byte, what the real launch path
(build_base_cmd + passthrough._build_pci_topology) emits with its sysfs lookups
mocked to the same NUMA layout — so offline measurements can't drift from a real
launch. Both paths emit a vfio-pci endpoint per root port; the measurement path
uses a placeholder BDF (later swapped for a pci-bar-stub), so the endpoint lines
are dropped from the comparison — only their BDF differs, and neither the BDF nor
the endpoint device type is settled here.
"""

from unittest.mock import patch

from chutes.guest.command import build_qemu_command
from chutes.guest.gpu.profiles import GPU_PROFILES
from chutes.guest.gpu.topology import FlatTopology, NumaTopology
from chutes.guest.passthrough import _build_pci_topology
from chutes.guest.qemu import build_base_cmd, use_numa_topology
from topology_spec import build_topology_spec, cpu_args_for_qemu_version

_FW = "OVMF.inteltdx.fd"


def _synth(profile, fingerprint):
    spec = build_topology_spec(
        profile, fingerprint, cpu_args="host,-avx10", firmware=_FW
    )
    return build_qemu_command(spec)


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
        out.append(cmd[i])
        i += 1
    return out


def _live_cmd(profile, *, node_by_bdf, host_nodes, gpus, nvsw=None, ib=None):
    """The command the real launch path produces, with sysfs lookups mocked."""
    with patch("chutes.guest.qemu.host_numa_nodes", return_value=host_nodes), patch(
        "chutes.guest.passthrough.read_pci_numa_node",
        side_effect=lambda b: node_by_bdf.get(b, -1),
    ):
        # Resolve nodes exactly as the launcher does: the mocked sysfs list
        # (host_nodes), gated by the profile's NUMA flag + the 2-node cap, then
        # hand build_base_cmd the explicit list (it no longer reads sysfs).
        numa_active = use_numa_topology(profile.enable_numa_topology)
        cmd = build_base_cmd(
            mem=f"{len(gpus) * profile.ram_per_gpu_gb}G",
            smp_topology=profile.smp_topology,
            process_name="chutes-measure",
            cpu_args="host,-avx10",
            firmware=_FW,
            img_path="root.qcow2",
            foreground=False,
            pidfile="/dev/null",
            logfile="/dev/null",
            host_nodes=host_nodes if numa_active else [],
        )
        _build_pci_topology(
            cmd,
            gpus=gpus,
            nvswitches_for_vm=nvsw or [],
            ib_devices=ib or [],
            profile=profile,
        )
    return cmd


def _bdfs(n, start=1):
    return [f"0000:{start + i:02x}:00.0" for i in range(n)]


def test_numa_4_4_matches_live_path():
    profile = GPU_PROFILES["RTX_PRO_6000"]
    fp = NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1))
    synth = _synth(profile, fp)

    gpus = _bdfs(8)
    live = _live_cmd(
        profile, node_by_bdf=dict(zip(gpus, fp.gpu_nodes)), host_nodes=[0, 1], gpus=gpus
    )
    assert _topology_args(synth) == _topology_args(live)
    assert any("pxb-pcie" in a for a in synth)
    # measurement emits a placeholder-BDF endpoint (never a real host device)
    assert any("vfio-pci,host=0000:00:00.0" in a for a in synth)


def test_numa_3_5_split_matches_live_path():
    profile = GPU_PROFILES["RTX_PRO_6000"]
    fp = NumaTopology(gpu_nodes=(0, 0, 0, 1, 1, 1, 1, 1))
    synth = _synth(profile, fp)

    gpus = _bdfs(8)
    live = _live_cmd(
        profile, node_by_bdf=dict(zip(gpus, fp.gpu_nodes)), host_nodes=[0, 1], gpus=gpus
    )
    assert _topology_args(synth) == _topology_args(live)


def test_flat_topology_matches_live_path_and_has_no_pxb():
    profile = GPU_PROFILES["RTX_PRO_6000"]
    fp = FlatTopology(gpu_count=8)
    synth = _synth(profile, fp)

    gpus = _bdfs(8)
    live = _live_cmd(profile, node_by_bdf={}, host_nodes=[0, 1, 2, 3], gpus=gpus)
    assert _topology_args(synth) == _topology_args(live)
    assert not any("pxb-pcie" in a for a in synth)


def test_h200_numa_with_nvswitches_matches_live_path():
    profile = GPU_PROFILES["H200"]
    fp = NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1), nvswitch_nodes=(1, 1, 1, 1))
    synth = _synth(profile, fp)

    gpus = _bdfs(8)
    nvsw = _bdfs(4, start=0x20)
    node_by_bdf = dict(zip(gpus, fp.gpu_nodes)) | dict(zip(nvsw, fp.nvswitch_nodes))
    live = _live_cmd(
        profile, node_by_bdf=node_by_bdf, host_nodes=[0, 1], gpus=gpus, nvsw=nvsw
    )
    assert _topology_args(synth) == _topology_args(live)
    assert any("rp_nvsw" in a for a in synth)


def test_cpu_args_for_qemu_version():
    assert cpu_args_for_qemu_version("10.2.1") == "host,-avx10"
    assert cpu_args_for_qemu_version("10.1.0") == "host,-avx10"
    assert cpu_args_for_qemu_version("99.9.9") == "host,-avx10"
