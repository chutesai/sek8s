"""The low-level TDX VM boot primitive — the raw QEMU boot with GPU-passthrough sizing.

Not a CLI command. Two entry points converge on ``launch_vm(args, host)``: `chutes-cvm guest
launch` calls it with the profile it already read and had preflight sign, and
``python -m chutes_cvm.guest`` runs ``main()`` for debugging, which reads the host itself.
Miners always use `chutes-cvm guest launch`.
"""

import argparse
import os
import signal
import sys
import time

from chutes_cvm import proc
from chutes_cvm.guest.detection import verify_host_qemu_supported
from chutes_cvm.guest.direct_boot import direct_boot_artifacts
from chutes_cvm.guest.gpu.profiles import (  # noqa: F401 — available for introspection
    GPU_PROFILES,
)
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.passthrough import attach_passthrough, bind_passthrough
from chutes_cvm.guest.post_launch import apply_post_launch_tuning
from chutes_cvm.guest.qemu import (
    PcieRootPinning,
    add_volumes,
    add_vsock,
    build_base_cmd,
    build_network,
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


def launch_vm(args, host: "HostProfile") -> int:

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
        if args.pass_gpus:
            print(
                f"  GPU passthrough: {host.gpu_count}x {profile.name}"
                f" ({profile.vram_gb}GB VRAM each)"
                f" → {vcpus} vCPUs, {mem} RAM"
            )
    elif args.pass_gpus:
        print("Error: --pass-gpus, but this host has no GPUs.", file=sys.stderr)
        return 1
    else:
        print(
            "  No GPUs on this host: debug guest, sized from the host; it cannot attest."
        )

    print(f"Launching confidential VM: {vcpus} vCPUs, {mem} RAM")
    print(f"Image: {args.image}")

    pci_pinning = PcieRootPinning(numa_active)
    print(f"TEE: {tee.label} ({tee.guest_id})")
    print(f"Firmware: {firmware}")

    # Direct boot (1.4.0+): OVMF boots the image's kernel/initrd directly, dropping
    # GRUB/shim from the measured chain. These are published with the image (built
    # once, downloaded from R2) and staged next to it — the same bytes
    # `measurements generate` measures, so the boot matches the pinned RTMR1/2.
    kernel_path, initrd_path, cmdline = direct_boot_artifacts(args.image)
    print(f"Direct boot: kernel={kernel_path} cmdline={cmdline!r}")

    qemu_cmds = build_base_cmd(
        host,
        process_name=PROCESS_NAME,
        firmware=firmware,
        img_path=args.image,
        foreground=args.foreground,
        pidfile=PIDFILE,
        logfile=LOGFILE,
        host_nodes=host_numa_nodes() if numa_active else [],
        pci_pinning=pci_pinning,
        kernel_path=kernel_path,
        initrd_path=initrd_path,
        cmdline=cmdline,
    )

    # Validation belongs to the launch, not the command builder: a launch without a host
    # interface is a misconfiguration, while a command built without one is exactly what
    # offline measurement generation needs.
    if args.network_type == "tap" and not args.net_iface:
        print("ERROR: --network-type tap requires --net-iface", file=sys.stderr)
        return 1

    build_network(
        qemu_cmds,
        network_type=args.network_type,
        net_iface=args.net_iface,
        ssh_port=args.ssh_port,
        net_queues=args.net_queues,
        pci_pinning=pci_pinning,
    )

    add_volumes(
        qemu_cmds,
        config_volume=args.config_volume,
        cache_volume=args.cache_volume,
        storage_volume=args.storage_volume,
        pci_pinning=pci_pinning,
    )

    add_vsock(qemu_cmds, pci_pinning=pci_pinning)

    if args.pass_gpus:
        # Bind first, then describe: the devices a launch attaches are created by binding
        # (SR-IOV VFs), and the command has to be able to name them.
        bind_passthrough(host)
        attach_passthrough(qemu_cmds, host)

    # Guest NUMA topology (numa_active) binds memory per node via QEMU
    # memory-backends, so no numactl prefix is needed. Otherwise interleave
    # across the GPUs' host NUMA nodes (all nodes if detection finds none).
    if numa_active:
        launch_prefix = []
    else:
        numa_nodes = (
            sorted({n for n in host.gpu_numa_nodes if n >= 0}) if args.pass_gpus else []
        )
        if numa_nodes:
            interleave = ",".join(str(n) for n in numa_nodes)
            print(f"  NUMA: interleaving memory across GPU nodes {interleave}")
        else:
            interleave = "all"
        launch_prefix = ["numactl", f"--interleave={interleave}"]

    print("Launching QEMU...")

    result = proc.run(
        launch_prefix + qemu_cmds.to_args(),
        stderr=proc.STDOUT,
    )
    if result.returncode != 0:
        print(f"Error: QEMU failed (exit {result.returncode}).", file=sys.stderr)
        return result.returncode

    if not args.foreground:
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

    if not args.foreground:
        print(f"Log file: {LOGFILE}")
    print_vm_status(args.ssh_port, show_ssh=args.ssh)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The boot primitive's flags.

    Exposed so `guest launch` gets argparse's defaults filled; a hand-built Namespace silently
    drops any flag it forgets, and the miss surfaces as an AttributeError mid-launch.
    """
    parser = argparse.ArgumentParser(
        prog="python -m chutes_cvm.guest",
        description="Low-level TDX VM boot primitive (driven by `chutes-cvm guest launch`).",
    )

    parser.add_argument("--image", type=str, help="Path to VM image")
    parser.add_argument("--pass-gpus", action="store_true")
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument(
        "--ssh",
        action="store_true",
        help="Show SSH login hint after launch (benchmark and debug modes)",
    )

    parser.add_argument("--config-volume", type=str)
    parser.add_argument("--cache-volume", type=str)
    parser.add_argument(
        "--storage-volume",
        type=str,
        help="Storage volume for VM storage (containerd and kubelet-pods)",
    )
    parser.add_argument("--ssh-port", type=int, default=10022)

    parser.add_argument("--network-type", choices=["tap", "user"], default="user")
    parser.add_argument("--net-iface", type=str)
    parser.add_argument(
        "--net-queues",
        type=int,
        default=4,
        help="Virtio-net multiqueue count for TAP mode (default: 4)",
    )

    return parser


def main(argv: "list[str] | None" = None) -> int:
    """Standalone entry point (``python -m chutes_cvm.guest``), for low-level debugging.

    Reads the host itself because nothing handed it one. `guest launch` does not come through
    here -- it calls ``launch_vm`` directly with the profile it already took.
    """
    args = build_parser().parse_args(argv)

    try:
        stop_existing_vm()
    except Exception:  # nosec B110
        pass

    if args.clean:
        return 0

    if not args.image:
        print("Error: --image is required")
        return 1

    host = HostProfile.from_host()
    return launch_vm(args, host)


if __name__ == "__main__":
    sys.exit(main())
