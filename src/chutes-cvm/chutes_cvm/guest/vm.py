"""The guest VM's QEMU process: start it, stop it, find it, and say whether another may start.

``launch_vm(guest, host)`` takes the two halves of a launch -- one ``LaunchContext`` (what this
guest needs) and one ``HostProfile`` (what this machine is) -- builds the command and runs it.
Everything else here is about that same process from the outside: the pidfile, the force-kill,
and what state a *previous* one has left the host in.

That last part is why this module is more than a launcher. A TD that powers off does not take
its QEMU with it: the last thread stays in ``do_exit`` reclaiming the guest's private memory,
holding every passthrough device's vfio file descriptor for as long as it runs (hours, for a
guest that faulted in a terabyte). Unbinding into that state blocks in uninterruptible D state
*and* costs the host its ability to reboot, so ``launch_blockers()`` is the one precondition
every launch checks before anything touches a device.

Not an entry point. ``chutes-cvm guest launch`` (``guest.launch``) is the only way to a guest:
it resolves config, takes THE reading of the host, gets a preflight verdict for that reading,
prepares volumes/image/network, and only then calls ``launch_vm``.

Force-kill lives here because it is the pidfile's business; graceful shutdown via the guest API
is ``guest.shutdown``. The QEMU *command* is ``guest.qemu``; this is the *process*.
"""

import os
import signal
import sys
import time
from dataclasses import dataclass

from chutes_cvm import proc, vfio
from chutes_cvm.guest.context import LaunchContext, host_numa_nodes
from chutes_cvm.guest.detection import verify_host_qemu_supported
from chutes_cvm.guest.gpu.profiles import (  # noqa: F401 — available for introspection
    GPU_PROFILES,
)
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.passthrough import bind_passthrough
from chutes_cvm.guest.post_launch import apply_post_launch_tuning
from chutes_cvm.guest.qemu import QemuCommand

# QEMU's -name/process= value, which sets the kernel's ``comm`` via prctl(PR_SET_NAME). It is
# the only identifier that survives the process becoming a zombie (``cmdline`` is emptied), so
# it is what detection matches on.
PROCESS_NAME = "chutes-td"

PIDFILE = "/tmp/tdx-td-pid.pid"  # nosec B108
LOGFILE = "/tmp/tdx-guest-td.log"  # nosec B108


# ────────────────────────────────────────────────────────────────────────────
# The running process
# ────────────────────────────────────────────────────────────────────────────


# prctl truncates the name to 15 characters; compare against the same truncation.
_COMM_MAX = 15

# Overridden in tests to point at a fixture tree.
PROC_ROOT = "/proc"


@dataclass(frozen=True)
class QemuProcess:
    """A guest QEMU and the threads still keeping its file table -- and its devices -- open."""

    pid: int
    leader_zombie: bool
    live_threads: tuple[str, ...]

    @property
    def alive(self) -> bool:
        """True while any thread remains: the only state in which the fds are still held."""
        return bool(self.live_threads)

    @property
    def tearing_down(self) -> bool:
        """The leader has been reaped but threads live on -- unkillable, and holding the devices.

        For a TDX guest this is the TD private-memory reclaim running in ``do_exit``; it ends on
        its own, and ``guest.tdreclaim`` says how long that will take.
        """
        return self.leader_zombie and self.alive


def _read_text(path: str) -> str | None:
    try:
        with open(path) as handle:
            return handle.read()
    except OSError:
        return None


def _task_state(task_dir: str) -> str | None:
    """Single-letter state from ``<task_dir>/stat``, or None if the task is gone.

    The comm field is parenthesised and may contain spaces, slashes and ')' -- QEMU names vCPU
    threads "CPU 62/KVM" -- so fields are taken after the *last* ')', never by splitting the
    whole line on whitespace.
    """
    stat = _read_text(f"{task_dir}/stat")
    if stat is None:
        return None
    _, sep, rest = stat.rpartition(")")
    if not sep:
        return None
    fields = rest.split()
    return fields[0] if fields else None


def _read_comm(task_dir: str) -> str | None:
    comm = _read_text(f"{task_dir}/comm")
    return comm.strip() if comm is not None else None


def read_qemu_process(pid: int) -> QemuProcess | None:
    """Read one pid's leader and per-thread states, or None if it is gone.

    A process that exits while being read reads as gone; callers treat that as "holds nothing".
    """
    pid_dir = f"{PROC_ROOT}/{pid}"
    leader = _task_state(pid_dir)
    if leader is None:
        return None
    try:
        tids = sorted(os.listdir(f"{pid_dir}/task"))
    except OSError:
        return None
    live: list[str] = []
    for tid in tids:
        task_dir = f"{pid_dir}/task/{tid}"
        state = _task_state(task_dir)
        if state is None or state == "Z":
            continue
        live.append(_read_comm(task_dir) or tid)
    return QemuProcess(pid=pid, leader_zombie=leader == "Z", live_threads=tuple(live))


def find_qemu_process(name: str = PROCESS_NAME) -> QemuProcess | None:
    """Scan /proc for a guest QEMU that still holds its fds, or None if none does.

    Reports a VM that is merely running as well as one mid-teardown: both hold the devices, and
    neither may be unbound underneath. Callers branch on ``QemuProcess.tearing_down``.
    """
    wanted = name[:_COMM_MAX]
    try:
        entries = sorted(os.listdir(PROC_ROOT))
    except OSError:
        return None
    for entry in entries:
        if not entry.isdigit():
            continue
        if _read_comm(f"{PROC_ROOT}/{entry}") != wanted:
            continue
        found = read_qemu_process(int(entry))
        if found is not None and found.alive:
            return found
    return None


# ────────────────────────────────────────────────────────────────────────────
# TD private-memory reclaim: how long until the devices are free
# ────────────────────────────────────────────────────────────────────────────


# Overridden in tests to point at a fixture tree.
KVM_DEBUGFS = "/sys/kernel/debug/kvm"

# Window for the two-sample rate read. Long enough that the rate is not noise, short enough to
# sit in front of a refusal the operator is waiting on.
SAMPLE_SECS = 10.0


@dataclass(frozen=True)
class Reclaim:
    """Progress of the TD private-memory reclaim, from KVM's 4KB-SPTE count."""

    pages_remaining: int
    pages_per_sec: float

    @property
    def gib_remaining(self) -> float:
        return self.pages_remaining * 4 / 1024 / 1024

    @property
    def eta_secs(self) -> float | None:
        """Seconds until the counter reaches zero, or None if it is not draining."""
        if self.pages_per_sec <= 0:
            return None
        return self.pages_remaining / self.pages_per_sec


def _sudo_lines(argv: list[str]) -> list[str]:
    try:
        result = proc.run(argv, capture_output=True, text=True, timeout=10)
    except (proc.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return result.stdout.split()


def _kvm_dirs() -> list[str]:
    """Entries under KVM's debugfs root. It is mode 0700, so fall back to sudo when not root."""
    try:
        return sorted(os.listdir(KVM_DEBUGFS))
    except OSError:
        return sorted(_sudo_lines(["sudo", "-n", "ls", KVM_DEBUGFS]))


def _read_counter(path: str) -> int | None:
    try:
        with open(path) as handle:
            raw: str | None = handle.read()
    except OSError:
        lines = _sudo_lines(["sudo", "-n", "cat", path])
        raw = lines[0] if lines else None
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def read_reclaim(pid: int, sample_secs: float = SAMPLE_SECS) -> Reclaim | None:
    """Sample KVM's 4KB-SPTE count twice to get pages remaining and the drain rate.

    KVM names its debugfs directory ``<qemu-pid>-<vm-fd>``. ``pages_4k`` is the live count of
    4KB SPTEs, which for a dying TD is dominated by its private memory, so it serves as the
    progress meter. Returns None when debugfs is absent, unreadable, or the VM has no directory
    (reclaim already finished).
    """
    prefix = f"{pid}-"
    name = next((d for d in _kvm_dirs() if d.startswith(prefix)), None)
    if name is None:
        return None
    path = f"{KVM_DEBUGFS}/{name}/pages_4k"
    first = _read_counter(path)
    if first is None:
        return None
    time.sleep(sample_secs)
    second = _read_counter(path)
    if second is None:
        return Reclaim(pages_remaining=first, pages_per_sec=0.0)
    drained = first - second
    rate = drained / sample_secs if drained > 0 else 0.0
    return Reclaim(pages_remaining=second, pages_per_sec=rate)


SYSRQ_RESET = (
    "    echo s > /proc/sysrq-trigger    # sync\n"
    "    echo u > /proc/sysrq-trigger    # remount read-only\n"
    "    echo b > /proc/sysrq-trigger    # reset (this IS the reboot)"
)


def _eta_phrase(reclaim: Reclaim | None) -> str:
    if reclaim is None:
        return (
            "  Progress is unavailable (KVM debugfs not readable), so there is no ETA. "
            "Reclaim takes roughly an hour per TB the guest had faulted in."
        )
    if reclaim.eta_secs is None:
        return (
            f"  {reclaim.pages_remaining:,} pages ({reclaim.gib_remaining:,.0f} GiB) remain, "
            "but the counter is not draining -- treat it as stalled."
        )
    return (
        f"  {reclaim.pages_remaining:,} pages ({reclaim.gib_remaining:,.0f} GiB) remain at "
        f"{reclaim.pages_per_sec:,.0f} pages/s -> ETA ~{reclaim.eta_secs / 60:,.0f} min."
    )


def reclaim_guidance(qemu: QemuProcess, reclaim: Reclaim | None) -> str:
    """Why a launch must not proceed while this reclaim runs, and the two ways forward."""
    shown = ", ".join(qemu.live_threads[:3])
    return (
        f"The previous VM's TD private-memory reclaim is still running (QEMU pid {qemu.pid} is "
        f"a zombie with {len(qemu.live_threads)} live thread(s): {shown}).\n"
        f"{_eta_phrase(reclaim)}\n"
        "  Refusing to touch the GPUs: unbinding now would block in uninterruptible D state "
        "until reclaim finishes, and those blocked unbinds also stop the host rebooting "
        "normally -- `reboot` would hang in device_shutdown().\n"
        "  Either wait for it to finish and launch again, or reset now, while a reboot still "
        "works. Nothing is lost -- the guest is already off and this memory is being handed "
        "back to the TDX module:\n"
        f"{SYSRQ_RESET}"
    )


# ────────────────────────────────────────────────────────────────────────────
# Launch safety: the one precondition every launch checks
# ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Blocker:
    """One reason a launch must not proceed, and what a caller can do about it."""

    name: str
    summary: str
    detail: str
    clears_itself: bool
    overridable: bool
    eta_secs: float | None = None


def _qemu_blocker() -> Blocker | None:
    """A guest QEMU still holding the passthrough devices: mid-reclaim, or simply running."""
    qemu = find_qemu_process()
    if qemu is None:
        return None

    if qemu.tearing_down:
        reclaim = read_reclaim(qemu.pid)
        eta = reclaim.eta_secs if reclaim is not None else None
        when = (
            f"~{eta / 60:,.0f} min remaining" if eta is not None else "duration unknown"
        )
        return Blocker(
            name="td-reclaim",
            summary=(
                f"QEMU pid {qemu.pid} is still reclaiming the previous TD's private memory "
                f"({when})"
            ),
            detail=reclaim_guidance(qemu, reclaim),
            clears_itself=True,
            overridable=False,
            eta_secs=eta,
        )

    return Blocker(
        name="qemu-running",
        summary=f"a confidential VM (QEMU pid {qemu.pid}) is already running",
        detail=(
            f"A confidential VM (QEMU pid {qemu.pid}) is already running and holds the "
            f"passthrough devices ({len(qemu.live_threads)} live threads).\n"
            "  Stop it first: chutes-cvm guest down  "
            "(or pass --force to override — not recommended)."
        ),
        clears_itself=False,
        overridable=True,
    )


def _pci_wedged_blocker() -> Blocker | None:
    """Uninterruptible D-state vfio/gpu-tools tasks from an earlier run.

    These are downstream of a reclaim, not independent of it: they are unbinds waiting on a
    QEMU that has not finished, and they clear when it does. They matter on their own because
    while they exist a plain ``reboot`` hangs in ``device_shutdown()`` -- so the operator needs
    to be told not to reach for one.
    """
    if not vfio.pci_operations_wedged():
        return None
    return Blocker(
        name="pci-wedged",
        summary="PCI operations are wedged by uninterruptible D-state tasks",
        detail=(
            "PCI operations are wedged: uninterruptible D-state tasks from an earlier vfio "
            "unbind or nvidia-gpu-tools run, so neither an unbind nor an SBR can run.\n"
            "  They are waiting on a QEMU that has not finished reclaiming the previous TD's "
            "memory, and clear on their own once it does.\n"
            "  A plain `reboot` will HANG in device_shutdown() while they exist. To reset now:\n"
            f"{SYSRQ_RESET}"
        ),
        clears_itself=True,
        overridable=False,
    )


# Order is the order a caller should report them: the QEMU is the cause, the wedged tasks are
# the symptom, and naming the cause first makes the pair legible.
_CHECKS = (_qemu_blocker, _pci_wedged_blocker)


def launch_blockers() -> list[Blocker]:
    """Every reason a launch must not touch this host's devices. Empty means safe to launch."""
    found = (check() for check in _CHECKS)
    return [blocker for blocker in found if blocker is not None]


def safe_to_launch() -> bool:
    """True if nothing blocks a launch. The single predicate; do not re-derive it."""
    return not launch_blockers()


# ────────────────────────────────────────────────────────────────────────────
# Lifecycle
# ────────────────────────────────────────────────────────────────────────────


def print_vm_status(tee_label: str, ssh_port: int, show_ssh: bool = False):
    try:
        with open(PIDFILE) as pid_file:
            pid = int(pid_file.read())
            print(f"{tee_label} VM running with PID: {pid}")
            if show_ssh:
                print("Login:")
                print(f"   ssh -p {ssh_port} root@<host-ip>")
    except Exception:  # nosec B110
        pass


def host_memory_placement(numa_nodes: "list[int]") -> "tuple[str, str]":
    """``numactl --interleave`` nodes for a flat guest's memory, and how to describe them.

    A guest without NUMA topology gets one memory backend, so where its memory lands is decided
    on the host: across the GPUs' NUMA nodes, or across every node when none were detected.
    """
    if not numa_nodes:
        return "all", "interleaved across all host NUMA nodes"
    nodes = ",".join(str(n) for n in numa_nodes)
    if len(numa_nodes) == 1:
        return nodes, f"on the GPUs' host NUMA node {nodes}"
    return nodes, f"interleaved across the GPUs' host NUMA nodes {nodes}"


def stop_existing_vm():
    print("Force-stopping VM (SIGTERM to QEMU)...")
    try:
        with open(PIDFILE) as pid_file:
            pid = int(pid_file.read().strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(3)
        os.remove(PIDFILE)
    except FileNotFoundError:
        pass


def launch_vm(guest: LaunchContext, host: "HostProfile") -> int:

    print("Starting confidential VM...")

    # The last line of defence before anything touches a device. `guest launch` checks this
    # first, but bind_passthrough below is the step that must never run against devices a
    # previous QEMU still holds, so the precondition is re-asserted next to it.
    blockers = launch_blockers()
    if blockers:
        for blocker in blockers:
            print(f"Error: {blocker.detail}", file=sys.stderr)
        return 1

    # Fail early if the host QEMU isn't the one baselined for its OS (moves RTMR0).
    verify_host_qemu_supported()

    # The platform follows from the profile's CPU vendor. Whether it is switched on is a
    # separate live question the provider answers, naming the BIOS setting rather than
    # failing opaquely inside QEMU -- and catching a profile captured on other hardware.
    tee = host.tee_provider
    tee.verify_environment()

    # Guest NUMA is a property of the host's nodes, not of any GPU. Only the value the
    # launcher itself needs (for PCIe pinning and the node list) is taken here; -cpu,
    # -smp and the memory size are read off the profile inside QemuCommand.build, so there
    # is no second copy of them to drift.
    numa_active = host.uses_guest_numa

    # Guest RAM already fits: HostProfile sizes it to aggregate VRAM clamped by what this host
    # can back, so there is nothing left to refuse here. A host too small for the full VRAM gets
    # a smaller guest and therefore its own class -- which is why the shape has to be registered
    # and measured before launch, and why preflight, not a RAM check, is what catches a host
    # whose guest nothing has measured.
    mem = host.mem
    vcpus = str(host.vcpus)

    if host.gpus:
        profile = host.gpu_profile
        if guest.pass_gpus:
            print(
                f"  GPU passthrough: {host.gpu_count}x {profile.name}"
                f" ({profile.vram_gb}GB VRAM each)"
                f" → {vcpus} vCPUs, {mem} RAM"
            )
    elif guest.pass_gpus:
        print("Error: --pass-gpus, but this host has no GPUs.", file=sys.stderr)
        return 1
    else:
        print(
            "  No GPUs on this host: debug guest, sized from the host; it cannot attest."
        )

    print(f"Launching confidential VM: {vcpus} vCPUs, {mem} RAM")
    print(f"Image: {guest.image}")

    print(f"TEE: {tee.label} ({tee.guest_id})")
    print(f"Firmware: {guest.firmware}")
    # Direct boot (1.4.0+): OVMF boots the image's kernel/initrd directly, dropping GRUB/shim from
    # the measured chain -- the same bytes `measurements generate` measures.
    print(f"Direct boot: kernel={guest.boot.kernel} cmdline={guest.kernel_cmdline!r}")

    # Validation belongs to the launch, not the command builder: a launch without a host
    # interface is a misconfiguration, while a command built without one is exactly what
    # offline measurement generation needs.
    if guest.network.network_type == "tap" and not guest.network.net_iface:
        print("ERROR: --network-type tap requires --net-iface", file=sys.stderr)
        return 1

    # Bind before building: the devices a launch attaches are created by binding (SR-IOV VFs),
    # and the command has to be able to name them.
    if guest.pass_gpus:
        bind_passthrough(host)

    cmd = QemuCommand.build(host, guest)

    # Guest NUMA topology (numa_active) binds memory per node via QEMU
    # memory-backends, so no numactl prefix is needed. Otherwise interleave
    # across the GPUs' host NUMA nodes (all nodes if detection finds none).
    if numa_active:
        launch_prefix = []
    else:
        numa_nodes = (
            sorted({n for n in host.gpu_numa_nodes if n >= 0})
            if guest.pass_gpus
            else []
        )
        interleave, where = host_memory_placement(numa_nodes)
        print(f"  Memory: flat guest (no guest NUMA); host memory {where}")
        launch_prefix = ["numactl", f"--interleave={interleave}"]

    print("Launching QEMU...")

    result = proc.run(
        launch_prefix + cmd.to_args(),
        stderr=proc.STDOUT,
    )
    if result.returncode != 0:
        print(f"Error: QEMU failed (exit {result.returncode}).", file=sys.stderr)
        return result.returncode

    if not guest.process.foreground:
        # vCPU thread pinning is gated on the profile enabling NUMA topology
        # (requires dual-socket host with PXB-PCIe grouping active). Host-wide
        # CPU power tuning is separate and operator-driven; see
        # `python -m chutes_cvm.host.tune` (chutes-cvm host tune / restore-host).
        pin_threads = (
            numa_active and profile is not None and profile.enable_post_launch_tuning
        )
        if pin_threads:
            apply_post_launch_tuning(
                pidfile=PIDFILE,
                vcpus_total=int(vcpus),
                host_nodes=host_numa_nodes(),
                pin_threads=pin_threads,
            )

    if not guest.process.foreground:
        print(f"Log file: {LOGFILE}")
    print_vm_status(
        host.tee_provider.label, guest.network.ssh_port, show_ssh=guest.show_ssh
    )
    return 0
