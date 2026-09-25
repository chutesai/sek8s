"""The guest VM's QEMU process: start it, force-kill it, report on it.

``launch_vm(guest, host)`` takes the two halves of a launch -- one ``GuestContext`` (what this
guest needs) and one ``HostProfile`` (what this machine is) -- builds the command and runs it.
``stop_existing_vm`` and ``print_vm_status`` operate on the same ``PIDFILE`` afterwards.

Not an entry point. ``chutes-cvm guest launch`` (``guest.launch``) is the only way to a guest:
it resolves config, takes THE reading of the host, gets a preflight verdict for that reading,
prepares volumes/image/network, and only then calls ``launch_vm``. This module used to carry an
argparse ``main()`` as well -- the interface the former quick-launch.sh called across the
bash->Python boundary. That shim now forwards to the CLI, so the interface had no callers left
and was a second way to boot a guest that skipped the attestation gate.

Force-kill lives here because it is the pidfile's business; graceful shutdown via the guest API
is ``guest.shutdown``.
"""

import os
import signal
import sys
import time

from chutes_cvm import proc
from chutes_cvm.guest.context import GuestContext
from chutes_cvm.guest.detection import verify_host_qemu_supported
from chutes_cvm.guest.direct_boot import direct_boot_artifacts
from chutes_cvm.guest.gpu.profiles import (  # noqa: F401 — available for introspection
    GPU_PROFILES,
)
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.passthrough import bind_passthrough
from chutes_cvm.guest.post_launch import apply_post_launch_tuning
from chutes_cvm.guest.qemu import (
    DirectBoot,
    PassthroughSet,
    ProcessBundle,
    QemuCommand,
    host_numa_nodes,
)
from chutes_cvm.paths import firmware_path

PIDFILE = "/tmp/tdx-td-pid.pid"  # nosec B108
LOGFILE = "/tmp/tdx-guest-td.log"  # nosec B108
PROCESS_NAME = "chutes-td"


def print_vm_status(ssh_port: int, show_ssh: bool = False):
    try:
        with open(PIDFILE) as pid_file:
            pid = int(pid_file.read())
            print(f"TDX VM running with PID: {pid}")
            if show_ssh:
                print("Login:")
                print(f"   ssh -p {ssh_port} root@<host-ip>")
    except Exception:  # nosec B110
        pass


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


def launch_vm(guest: GuestContext, host: "HostProfile") -> int:

    print("Starting confidential VM...")

    # Fail early if the host QEMU isn't the one baselined for its OS (moves RTMR0).
    verify_host_qemu_supported()

    # The platform follows from the profile's CPU vendor. Whether it is switched on is a
    # separate live question the provider answers, naming the BIOS setting rather than
    # failing opaquely inside QEMU -- and catching a profile captured on other hardware.
    tee = host.tee_provider
    tee.verify_environment()

    # Guest NUMA is a property of the host's nodes, not of any GPU. Only the value the
    # launcher itself needs (for PCIe pinning and the node list) is taken here; -cpu,
    # -smp and the memory size are read off the profile inside build_base_cmd, so there
    # is no second copy of them to drift.
    numa_active = host.uses_guest_numa

    # Guest RAM already fits: HostProfile sizes it to aggregate VRAM clamped by what this host
    # can back, so there is nothing left to refuse here. A host too small for the full VRAM gets
    # a smaller guest and therefore its own class -- which is why the shape has to be registered
    # and measured before launch, and why preflight, not a RAM check, is what catches a host
    # whose guest nothing has measured.
    mem = host.mem
    vcpus = str(host.vcpus)

    # Firmware is a property of the platform, not the GPU profile: TDX boots the pinned
    # TDVF, SNP the AMD OVMF build. Both are measured, so both are pinned in the repo.
    firmware = firmware_path(tee.default_firmware)

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
    print(f"Firmware: {firmware}")

    # Direct boot (1.4.0+): OVMF boots the image's kernel/initrd directly, dropping
    # GRUB/shim from the measured chain. These are published with the image (built
    # once, downloaded from R2) and staged next to it — the same bytes
    # `measurements generate` measures, so the boot matches the pinned RTMR1/2.
    kernel_path, initrd_path, cmdline = direct_boot_artifacts(guest.image)
    print(f"Direct boot: kernel={kernel_path} cmdline={cmdline!r}")

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

    cmd = QemuCommand.create(
        host,
        firmware=firmware,
        img_path=guest.image,
        host_nodes=host_numa_nodes() if numa_active else [],
        boot=DirectBoot(kernel_path, initrd_path, cmdline),
        net=guest.network,
        volumes=guest.volumes,
        process=ProcessBundle(PROCESS_NAME, guest.foreground, PIDFILE, LOGFILE),
        # --no-gpus leaves the GPUs bound to their host driver, so a command that named them
        # would be one QEMU refuses.
        passthrough=(
            PassthroughSet.from_profile(host) if guest.pass_gpus else PassthroughSet()
        ),
    )

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
        if numa_nodes:
            interleave = ",".join(str(n) for n in numa_nodes)
            print(f"  NUMA: interleaving memory across GPU nodes {interleave}")
        else:
            interleave = "all"
        launch_prefix = ["numactl", f"--interleave={interleave}"]

    print("Launching QEMU...")

    result = proc.run(
        launch_prefix + cmd.to_args(),
        stderr=proc.STDOUT,
    )
    if result.returncode != 0:
        print(f"Error: QEMU failed (exit {result.returncode}).", file=sys.stderr)
        return result.returncode

    if not guest.foreground:
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

    if not guest.foreground:
        print(f"Log file: {LOGFILE}")
    print_vm_status(guest.network.ssh_port, show_ssh=guest.show_ssh)
    return 0
