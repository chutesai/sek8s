"""Tests for chutes_cvm.guest.vm — the guest VM's QEMU process.

Not an entry point: `chutes-cvm guest launch` is the only way to a guest, and it calls
``launch_vm`` with a context and a profile it has already gated."""

from unittest.mock import MagicMock, patch

import chutes_cvm.guest.tee as tee_module
import chutes_cvm.guest.vm as vm
import pytest
from chutes_cvm.guest.context import (
    DirectBoot,
    GuestNetwork,
    GuestVolumes,
    LaunchContext,
    PassthroughSet,
    ProcessBundle,
    TdxLaunchContext,
)
from chutes_cvm.guest.detection import GUEST_CPU_ARGS
from chutes_cvm.guest.qemu import QemuCommand
from chutes_cvm.paths import SCRIPTS_DIR


def _guest(**over) -> LaunchContext:
    """A user-mode debug guest: the smallest context the primitive accepts."""
    return TdxLaunchContext(
        image="/tmp/fake.img",
        firmware="/fw/OVMF.fd",
        host_nodes=(),
        boot=DirectBoot("/k", "/i", "root=UUID=x ro"),
        network=GuestNetwork(network_type="user", ssh_port=10022),
        volumes=GuestVolumes(),
        process=ProcessBundle(name="chutes-td", foreground=True),
        passthrough=PassthroughSet(),
        pass_gpus=over.get("pass_gpus", False),
    )


_FAKE_CMD = QemuCommand(
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


# The provider comes from the profile the caller hands in (an Intel doc -> TDX), so only the
# CAPABILITY check is stubbed: it reads this machine's kvm module parameters, which are a
# property of the box running the tests, not of the code under test.
@patch("chutes_cvm.guest.tee.TeeProvider.verify_environment")
@patch("chutes_cvm.guest.vm.verify_host_qemu_supported")
@patch("chutes_cvm.guest.vm.proc.run")
@patch("chutes_cvm.guest.vm.bind_passthrough")
@patch("chutes_cvm.guest.vm.QemuCommand.build", return_value=_FAKE_CMD)
def test_launch_vm_returns_qemu_nonzero(
    _mock_create,
    _mock_bind,
    mock_run,
    _mock_qemu_check,
    _mock_tee,
):
    import topology_fixtures as known
    from chutes_cvm.guest.host_profile import HostProfile

    host = HostProfile.from_dict(known.rtx_numa_doc())
    mock_run.return_value = MagicMock(returncode=1)
    assert vm.launch_vm(_guest(), host) == 1


@patch("chutes_cvm.guest.vm.verify_host_qemu_supported")
@patch("chutes_cvm.guest.vm.proc.run")
@patch("chutes_cvm.guest.vm.bind_passthrough")
@patch("chutes_cvm.guest.vm.QemuCommand.build", return_value=_FAKE_CMD)
# As above: the profile decides the platform; this only stubs the host capability probe.
@patch("chutes_cvm.guest.tee.TeeProvider.verify_environment")
def test_launch_takes_cpu_args_from_the_host_profile(
    _mock_tee2,
    mock_create,
    _mock_bind,
    mock_run,
    _mock_qemu_check,
    monkeypatch,
):
    """A launch must pass the -cpu the profile resolves, as generation does.

    Both go through `HostProfile.cpu_args`. The launcher used the bare GUEST_CPU_ARGS constant,
    which agreed only because the table holds a single entry mapping to that same constant -- so
    the day a second QEMU version maps to different args, a host on it
    would boot with one -cpu having been measured with another, and fail attestation with nothing
    in the command to show why.
    """
    import topology_fixtures as known
    from chutes_cvm.guest.host_profile import HostProfile

    monkeypatch.setitem(HostProfile.CPU_ARGS_BY_QEMU, "11.0.0", "host,-avx10,-tsx")
    doc = known.rtx_numa_doc()
    doc["qemu"]["qemu_version"] = "11.0.0"
    host = HostProfile.from_dict(doc)
    mock_run.return_value = MagicMock(returncode=0)

    vm.launch_vm(
        _guest(pass_gpus=True),
        host,
    )

    # The launcher hands over the profile itself rather than a copy of its -cpu, so the
    # assertion is that the profile reaching the factory resolves the right args. That is
    # structural -- build() reads them off the profile -- but the launcher could still pass
    # the wrong profile, which is what this catches.
    passed_profile = mock_create.call_args.args[0]
    assert passed_profile.cpu_args == "host,-avx10,-tsx"
    assert passed_profile.cpu_args != GUEST_CPU_ARGS


def test_discover_profile_reports_the_launch_cpu_args():
    """discover-profile.sh re-spells the -cpu args in bash. They feed the host profile the
    control plane fingerprints, so a drift from what the launcher actually passes would
    baseline a class against CPUID leaves no VM ever boots with."""
    script = (SCRIPTS_DIR / "discover-profile.sh").read_text()
    assert f'CPU_ARGS="{GUEST_CPU_ARGS}"' in script


@patch("chutes_cvm.guest.vm.verify_host_qemu_supported")
@patch(
    "chutes_cvm.guest.tee._module_param_enabled",
    side_effect=lambda path: path == tee_module.KVM_INTEL_TDX,
)
def test_launch_refuses_when_the_host_contradicts_the_profile(
    _mock_param, _mock_qemu_check
):
    """An AMD profile on a machine reporting TDX means the profile was captured
    elsewhere, or SEV-SNP is off in BIOS. Either way the guest would be measured
    against a platform it is not booting on, so refuse before any VM work.

    The real ``verify_environment`` runs here -- only the kvm parameter read is stubbed,
    since that is a property of the box running the tests, not of the code under test.
    """
    import topology_fixtures as known
    from chutes_cvm.guest.host_profile import HostProfile

    doc = known.rtx_numa_doc()
    doc["cpu"]["vendor"] = "AuthenticAMD"
    host = HostProfile.from_dict(doc)

    with pytest.raises(RuntimeError, match="but the machine reports"):
        vm.launch_vm(
            _guest(),
            host,
        )


@pytest.mark.parametrize(
    "nodes, interleave, where",
    [
        ([0, 1], "0,1", "interleaved across the GPUs' host NUMA nodes 0,1"),
        ([0], "0", "on the GPUs' host NUMA node 0"),
        ([], "all", "interleaved across all host NUMA nodes"),
    ],
)
def test_flat_guest_memory_placement_says_where_it_lands(nodes, interleave, where):
    """A single node is not "interleaved": the message used to say so on one-node hosts."""
    assert vm.host_memory_placement(nodes) == (interleave, where)


@pytest.mark.parametrize("label", ["Intel TDX", "AMD SEV-SNP"])
def test_vm_status_names_the_guests_tee(tmp_path, monkeypatch, capsys, label):
    pidfile = tmp_path / "pid"
    pidfile.write_text("4242")
    monkeypatch.setattr(vm, "PIDFILE", str(pidfile))
    vm.print_vm_status(label, 10022)
    assert capsys.readouterr().out.strip() == f"{label} VM running with PID: 4242"


# ────────────────────────────────────────────────────────────────────────────
# Finding the QEMU process and reading its threads
# ────────────────────────────────────────────────────────────────────────────


def _write_task(task_dir, tid, state, comm):
    task_dir.mkdir(parents=True, exist_ok=True)
    # "<pid> (<comm>) <state> <rest>" -- comm is parenthesised and may contain spaces and ')'.
    (task_dir / "stat").write_text(f"{tid} ({comm}) {state} 1 2 3 4 5\n")
    (task_dir / "comm").write_text(f"{comm}\n")


def _make_proc(root, pid, leader_state, threads, comm=vm.PROCESS_NAME):
    """Build <root>/<pid> with a leader and threads = [(tid, state, comm), ...]."""
    pid_dir = root / str(pid)
    _write_task(pid_dir, pid, leader_state, comm)
    for tid, state, tcomm in threads:
        _write_task(pid_dir / "task" / str(tid), tid, state, tcomm)
    return pid_dir


@pytest.fixture
def fake_proc(tmp_path, monkeypatch):
    root = tmp_path / "proc"
    root.mkdir()
    monkeypatch.setattr(vm, "PROC_ROOT", str(root))
    return root


def _reclaiming(root, pid=56677, tid=56756):
    """The observed shape: zombie leader, one live vCPU thread doing the reclaim."""
    return _make_proc(
        root,
        pid,
        "Z",
        [(pid, "Z", vm.PROCESS_NAME), (tid, "R", "CPU 62/KVM")],
    )


# ---------------------------------------------------------------------------
# Process detection
# ---------------------------------------------------------------------------


def test_zombie_leader_with_live_thread_is_tearing_down(fake_proc):
    _reclaiming(fake_proc)
    found = vm.find_qemu_process()
    assert found is not None
    assert found.pid == 56677
    assert found.alive and found.tearing_down
    assert found.live_threads == ("CPU 62/KVM",)


def test_empty_cmdline_does_not_hide_it(fake_proc):
    # The regression this module exists for: nothing may read cmdline, because a zombie has
    # none. The fixture deliberately provides no cmdline file at all.
    pid_dir = _reclaiming(fake_proc)
    assert not (pid_dir / "cmdline").exists()
    assert vm.find_qemu_process() is not None


def test_single_running_thread_also_counts(fake_proc):
    # Also observed: leader itself still R with nlwp=1, late in the same teardown.
    _make_proc(fake_proc, 5845, "R", [(5845, "R", vm.PROCESS_NAME)])
    found = vm.find_qemu_process()
    assert found is not None and found.alive
    assert (
        not found.tearing_down
    )  # leader is not a zombie, so it reads as "still running"


def test_reaped_zombie_holds_nothing(fake_proc):
    # Leader zombie, no surviving threads: the file table is already released, so this blocks
    # nothing and must not be reported.
    _make_proc(fake_proc, 56677, "Z", [(56677, "Z", vm.PROCESS_NAME)])
    assert vm.find_qemu_process() is None


def test_healthy_running_vm_is_reported_too(fake_proc):
    # A live VM also holds the devices; a launch must not unbind underneath it either.
    _make_proc(
        fake_proc,
        4100,
        "S",
        [(4100, "S", vm.PROCESS_NAME), (4101, "S", "CPU 0/KVM")],
    )
    found = vm.find_qemu_process()
    assert found is not None and found.alive and not found.tearing_down


def test_other_processes_and_non_numeric_entries_ignored(fake_proc):
    _make_proc(fake_proc, 900, "S", [(900, "S", "sshd")], comm="sshd")
    (fake_proc / "self").mkdir()
    assert vm.find_qemu_process() is None


def test_missing_pid_inspects_as_gone(fake_proc):
    assert vm.read_qemu_process(1234) is None


def test_thread_comm_with_parens_and_spaces_parses(fake_proc):
    # Splitting /proc/<pid>/stat on whitespace breaks on these; state is read after the LAST ')'.
    _make_proc(
        fake_proc,
        7000,
        "Z",
        [(7000, "Z", vm.PROCESS_NAME), (7001, "R", "CPU 8/KVM (x)")],
    )
    found = vm.find_qemu_process()
    assert found is not None and found.live_threads == ("CPU 8/KVM (x)",)


def test_comm_compared_at_prctl_truncation(fake_proc):
    long_name = "chutes-td-with-a-long-name"
    _make_proc(fake_proc, 8000, "S", [(8000, "S", long_name[:15])], comm=long_name[:15])
    found = vm.find_qemu_process(long_name)
    assert found is not None and found.pid == 8000


# ────────────────────────────────────────────────────────────────────────────
# TD private-memory reclaim progress and guidance
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_debugfs(tmp_path, monkeypatch):
    root = tmp_path / "kvm"
    root.mkdir()
    monkeypatch.setattr(vm, "KVM_DEBUGFS", str(root))
    return root


# ---------------------------------------------------------------------------
# Reclaim progress
# ---------------------------------------------------------------------------


def test_read_reclaim_computes_rate_and_eta(fake_debugfs, monkeypatch):
    vmdir = fake_debugfs / "56677-16"
    vmdir.mkdir()
    counter = vmdir / "pages_4k"
    counter.write_text("187134563\n")

    # Second sample lands after the (mocked) sleep: 769,707 pages drained.
    def _sleep(_secs):
        counter.write_text("186364856\n")

    monkeypatch.setattr(vm.time, "sleep", _sleep)
    r = vm.read_reclaim(56677, sample_secs=30.0)
    assert r is not None
    assert r.pages_remaining == 186364856
    assert r.pages_per_sec == pytest.approx(769707 / 30.0)
    assert r.eta_secs == pytest.approx(186364856 / (769707 / 30.0))
    assert r.gib_remaining == pytest.approx(186364856 * 4 / 1024 / 1024)


def test_read_reclaim_none_when_vm_has_no_debugfs_dir(fake_debugfs):
    # Reclaim already finished -> KVM removed the directory.
    assert vm.read_reclaim(56677, sample_secs=0) is None


def test_read_reclaim_matches_only_the_right_pid(fake_debugfs, monkeypatch):
    other = fake_debugfs / "999-3"
    other.mkdir()
    (other / "pages_4k").write_text("5\n")
    monkeypatch.setattr(vm.time, "sleep", lambda _s: None)
    assert vm.read_reclaim(56677, sample_secs=1.0) is None


def test_flat_counter_reports_no_eta(fake_debugfs, monkeypatch):
    vmdir = fake_debugfs / "42-1"
    vmdir.mkdir()
    (vmdir / "pages_4k").write_text("1000\n")
    monkeypatch.setattr(vm.time, "sleep", lambda _s: None)
    r = vm.read_reclaim(42, sample_secs=10.0)
    assert r is not None and r.pages_per_sec == 0.0 and r.eta_secs is None


# ---------------------------------------------------------------------------
# Guidance
# ---------------------------------------------------------------------------


def _vm():
    return vm.QemuProcess(pid=56677, leader_zombie=True, live_threads=("CPU 62/KVM",))


def test_guidance_gives_eta_and_the_sysrq_escape():
    msg = vm.reclaim_guidance(
        _vm(), vm.Reclaim(pages_remaining=158197793, pages_per_sec=25780.0)
    )
    assert "56677" in msg and "CPU 62/KVM" in msg
    assert "102 min" in msg  # 158197793 / 25780 / 60
    assert "603 GiB" in msg
    assert "sysrq-trigger" in msg
    assert "device_shutdown()" in msg  # says why a plain reboot is not the answer


def test_guidance_without_progress_still_warns_and_escapes():
    msg = vm.reclaim_guidance(_vm(), None)
    assert "no ETA" in msg
    assert "sysrq-trigger" in msg


def test_guidance_for_a_stalled_counter_says_so():
    msg = vm.reclaim_guidance(
        _vm(), vm.Reclaim(pages_remaining=5000, pages_per_sec=0.0)
    )
    assert "stalled" in msg
    assert "sysrq-trigger" in msg


# ────────────────────────────────────────────────────────────────────────────
# launch_blockers(): the one safe-to-launch predicate
# ────────────────────────────────────────────────────────────────────────────


S = "chutes_cvm.guest.vm"


@pytest.fixture
def clear_host(monkeypatch):
    """A host with nothing blocking, so each predicate test opts in to exactly one blocker.

    Deliberately not autouse: this file also tests the /proc scan itself, which needs the real
    find_qemu_process.
    """
    monkeypatch.setattr(f"{S}.find_qemu_process", lambda *a, **k: None)
    monkeypatch.setattr(f"{S}.vfio.pci_operations_wedged", lambda *a, **k: False)


def _qemu(pid=56677, *, tearing_down):
    return vm.QemuProcess(
        pid=pid, leader_zombie=tearing_down, live_threads=("CPU 62/KVM",)
    )


def _found(monkeypatch, qemu, reclaim=None):
    monkeypatch.setattr(f"{S}.find_qemu_process", lambda *a, **k: qemu)
    monkeypatch.setattr(f"{S}.read_reclaim", lambda *a, **k: reclaim)


# ---------------------------------------------------------------------------
# A clear host
# ---------------------------------------------------------------------------


def test_clear_host_has_no_blockers(clear_host):
    assert vm.launch_blockers() == []
    assert vm.safe_to_launch()


# ---------------------------------------------------------------------------
# TD reclaim
# ---------------------------------------------------------------------------


def test_reclaim_is_not_overridable_and_carries_its_eta(monkeypatch, clear_host):
    _found(
        monkeypatch,
        _qemu(tearing_down=True),
        vm.Reclaim(pages_remaining=158197793, pages_per_sec=25780.0),
    )
    (blocker,) = vm.launch_blockers()
    assert blocker.name == "td-reclaim"
    assert not blocker.overridable  # forcing past this reaches the unbind
    assert blocker.clears_itself  # so waiting is a real option
    assert blocker.eta_secs == pytest.approx(158197793 / 25780.0)
    assert "102 min" in blocker.summary
    assert "sysrq-trigger" in blocker.detail
    assert not vm.safe_to_launch()


def test_reclaim_without_progress_still_blocks(monkeypatch, clear_host):
    # debugfs unreadable -> no ETA, but the refusal must not depend on having one.
    _found(monkeypatch, _qemu(tearing_down=True), None)
    (blocker,) = vm.launch_blockers()
    assert blocker.name == "td-reclaim"
    assert blocker.eta_secs is None
    assert "duration unknown" in blocker.summary
    assert not blocker.overridable


# ---------------------------------------------------------------------------
# A VM that is merely running
# ---------------------------------------------------------------------------


def test_running_vm_is_overridable_and_needs_a_human(monkeypatch, clear_host):
    _found(monkeypatch, _qemu(pid=4100, tearing_down=False))
    (blocker,) = vm.launch_blockers()
    assert blocker.name == "qemu-running"
    assert blocker.overridable  # the operator's call
    assert not blocker.clears_itself  # nobody is going to stop it for you
    assert "chutes-cvm guest down" in blocker.detail
    assert (
        "sysrq-trigger" not in blocker.detail
    )  # nothing is wedged; do not offer a reset


# ---------------------------------------------------------------------------
# Wedged PCI
# ---------------------------------------------------------------------------


def test_wedged_pci_blocks_and_warns_against_a_plain_reboot(monkeypatch, clear_host):
    monkeypatch.setattr(f"{S}.vfio.pci_operations_wedged", lambda *a, **k: True)
    (blocker,) = vm.launch_blockers()
    assert blocker.name == "pci-wedged"
    assert not blocker.overridable
    assert (
        blocker.clears_itself
    )  # the unbinds clear when the reclaim they wait on finishes
    assert "device_shutdown()" in blocker.detail
    assert "sysrq-trigger" in blocker.detail


def test_cause_is_reported_before_symptom(monkeypatch, clear_host):
    """Both at once is the common case: the reclaim is why the unbinds are stuck, so naming it
    first is what makes the pair legible."""
    _found(monkeypatch, _qemu(tearing_down=True), None)
    monkeypatch.setattr(f"{S}.vfio.pci_operations_wedged", lambda *a, **k: True)
    assert [b.name for b in vm.launch_blockers()] == [
        "td-reclaim",
        "pci-wedged",
    ]


# ────────────────────────────────────────────────────────────────────────────
# launch_vm refuses before it binds
# ────────────────────────────────────────────────────────────────────────────


@patch("chutes_cvm.guest.vm.verify_host_qemu_supported")
@patch("chutes_cvm.guest.vm.proc.run")
@patch("chutes_cvm.guest.vm.bind_passthrough")
@patch("chutes_cvm.guest.tee.TeeProvider.verify_environment")
def test_launch_vm_refuses_before_binding_when_blocked(
    _mock_tee, mock_bind, mock_run, _mock_qemu_check, monkeypatch, capsys
):
    """bind_passthrough is the step that must never run against devices a previous QEMU still
    holds, so the precondition sits immediately in front of it -- and nothing, including QEMU
    itself, may be reached past it."""
    import topology_fixtures as known
    from chutes_cvm.guest.host_profile import HostProfile

    monkeypatch.setattr(
        f"{S}.launch_blockers",
        lambda: [
            vm.Blocker(
                name="td-reclaim",
                summary="reclaiming",
                detail="reclaim running; ETA ~102 min\n    echo b > /proc/sysrq-trigger",
                clears_itself=True,
                overridable=False,
            )
        ],
    )

    rc = vm.launch_vm(_guest(), HostProfile.from_dict(known.rtx_numa_doc()))

    assert rc == 1
    mock_bind.assert_not_called()
    mock_run.assert_not_called()
    err = capsys.readouterr().err
    assert "102 min" in err and "sysrq-trigger" in err
