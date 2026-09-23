"""The passthrough split: binding devices and describing them are separate jobs.

They were one function. Binding is a privileged mutation of the live machine (driver rebinds,
SR-IOV VF creation); describing is pure arithmetic over the captured profile that lands in the
DSDT and so in RTMR0. Keeping them apart is what lets the command be built without touching
hardware -- and what will let offline measurement generation share the same builder.
"""

import topology_fixtures as known
from chutes_cvm.guest import passthrough
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.passthrough import attach_passthrough, bind_passthrough
from chutes_cvm.guest.qemu import QemuCommand

P = "chutes_cvm.guest.passthrough"


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


def test_attach_passthrough_touches_no_device(monkeypatch):
    """The command half must be safe to run with no hardware -- that is what makes one
    builder usable by both the launcher and offline generation."""
    called = []
    monkeypatch.setattr(
        P + "._prepare_devices", lambda *a, **k: called.append("prepare")
    )
    monkeypatch.setattr(
        passthrough, "detect_infiniband_vfs", lambda *a, **k: called.append("vfs") or []
    )
    cmd = _cmd()

    attach_passthrough(cmd, HostProfile(known.h200_doc()))

    assert called == []
    assert "iommufd,id=iommufd0" in cmd.objects
    assert any("pxb-pcie" in d for d in cmd.devices)


def test_attach_passthrough_is_a_noop_without_gpus():
    """A debug guest binds nothing and gets no iommufd object, exactly as before the split."""
    doc = known.h200_doc()
    doc["gpus"] = []
    cmd = _cmd()

    attach_passthrough(cmd, HostProfile(doc))

    assert cmd.objects == [] and cmd.devices == []
