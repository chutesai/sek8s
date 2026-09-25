"""The passthrough split: binding devices and describing them are separate jobs.

They were one function. Binding is a privileged mutation of the live machine (driver rebinds,
SR-IOV VF creation); describing is pure arithmetic over the captured profile that lands in the
DSDT and so in RTMR0. Keeping them apart is what lets the command be built without touching
hardware -- and what will let offline measurement generation share the same builder.
"""

import topology_fixtures as known
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.passthrough import bind_passthrough
from chutes_cvm.guest.qemu import (
    IOMMUFD_ID,
    DirectBoot,
    GuestNetwork,
    GuestVolumes,
    PassthroughSet,
    ProcessBundle,
    QemuCommand,
)

P = "chutes_cvm.guest.passthrough"


def _create_args() -> dict:
    """The inputs create() requires. Spelled out rather than defaulted: create() deliberately
    defaults none of the values that differ between a launch and a measurement."""
    return dict(
        firmware="/x",
        img_path="/i",
        host_nodes=[0, 1],
        boot=DirectBoot(kernel="/k", initrd="/i", cmdline="root=UUID=x ro"),
        net=GuestNetwork(network_type="user", ssh_port=10022),
        volumes=GuestVolumes(),
        process=ProcessBundle(name="chutes-td"),
    )


def _cmd() -> QemuCommand:
    return QemuCommand(
        mem="1G",
        smp_topology="1",
        cpu_args="host",
        machine="q35",
        firmware="/x",
        process_name="t",
        foreground=True,
        logfile="/l",
        pidfile="/p",
    )


def test_bind_passthrough_takes_no_command():
    """Structural, and the point of the split: binding cannot contribute to the command even
    by accident, because it is handed no command to contribute to."""
    import inspect

    assert list(inspect.signature(bind_passthrough).parameters) == ["host"]


def test_bind_passthrough_prepares_the_profile_devices(monkeypatch):
    prepared = {}
    monkeypatch.setattr(
        P + "._prepare_devices",
        lambda gpus, nvswitches, ib_devices, profile: prepared.update(
            gpus=gpus, nvswitches=nvswitches, ib=ib_devices
        ),
    )
    host = HostProfile(known.h200_doc())

    bind_passthrough(host)

    assert prepared["gpus"] == [d.bdf for d in host.gpus]
    # The host's FULL NVSwitch inventory is bound; which ones reach the guest is a separate
    # question that attach_passthrough answers from the profile.
    assert prepared["nvswitches"] == [d.bdf for d in host.attached_nvswitches]


def test_iommufd_follows_the_passthrough_set():
    """Every endpoint build_pci_topology emits carries `iommufd=iommufd0`, so the object is not a
    separate decision: a command with passthrough devices and no such object is one QEMU refuses,
    and one with the object and no devices declares a handle nothing uses."""
    host = HostProfile(known.h200_doc())

    with_devices = QemuCommand.create(
        host, **_create_args(), passthrough=PassthroughSet.from_profile(host)
    )
    without = QemuCommand.create(host, **_create_args(), passthrough=PassthroughSet())

    assert f"iommufd,id={IOMMUFD_ID}" in with_devices.objects
    assert all(
        f"iommufd={IOMMUFD_ID}" in d for d in with_devices.devices if "vfio-pci" in d
    )
    assert not any("iommufd" in o for o in without.objects)


def test_create_attaches_topology_without_touching_a_device(monkeypatch):
    """The command half must be safe to run with no hardware -- that is what lets one builder
    serve both the launcher and offline generation."""
    called = []
    monkeypatch.setattr(
        P + "._prepare_devices", lambda *a, **k: called.append("prepare")
    )
    host = HostProfile(known.h200_doc())

    cmd = QemuCommand.create(
        host, **_create_args(), passthrough=PassthroughSet.from_profile(host)
    )

    assert called == []
    assert any("pxb-pcie" in d for d in cmd.devices)


def test_create_with_an_empty_passthrough_set_names_no_devices():
    """`--no-gpus` leaves the GPUs on their host driver, so the command must not name them."""
    host = HostProfile(known.h200_doc())

    cmd = QemuCommand.create(host, **_create_args(), passthrough=PassthroughSet())

    assert not any("vfio-pci" in d or "pxb-pcie" in d for d in cmd.devices)
