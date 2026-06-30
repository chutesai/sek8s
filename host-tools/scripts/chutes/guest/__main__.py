"""CLI entry point for TDX VM launch.

Invoked via: python3 ./run-td [args]
"""

import argparse
import os
import platform
import signal
import subprocess
import sys
import time

from chutes.guest.detection import (
    detect_gpu_numa_nodes,
    detect_host_mem_gb,
    detect_nvidia_gpus,
    detect_profile,
    get_gpu_bdfs,
    verify_host_qemu_supported,
)
from chutes.guest.gpu.profiles import GPU_PROFILES  # noqa: F401 — available for introspection
from chutes.guest.passthrough import setup_passthrough
from chutes.guest.post_launch import apply_post_launch_tuning
from chutes.guest.qemu import (
    PcieRootPinning,
    add_volumes,
    add_vsock,
    build_base_cmd,
    build_network,
    host_numa_nodes,
    safe_vm_mem_gb,
    use_numa_topology,
)

PIDFILE = "/tmp/tdx-td-pid.pid"
LOGFILE = "/tmp/tdx-guest-td.log"
PROCESS_NAME = "chutes-td"

DEFAULT_MEM = "100G"
DEFAULT_VCPUS = "32"

# TDVF MUST NOT be overridden by user config (MRTD depends on it).
# The filename is selected per GPU profile; see GpuProfile.firmware_filename.
_DEFAULT_FIRMWARE = "OVMF.inteltdx.fd"


def _firmware_path(filename: str = _DEFAULT_FIRMWARE) -> str:
    scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(scripts_dir, "../../firmware", filename)


def print_vm_status(ssh_port: int, show_ssh: bool = False):
    try:
        with open(PIDFILE) as pid_file:
            pid = int(pid_file.read())
            print(f"TDX VM running with PID: {pid}")
            if show_ssh:
                print(f"Login:")
                print(f"   ssh -p {ssh_port} root@<host-ip>")
    except Exception:
        pass


def stop_existing_vm():
    print("Clean VM")
    try:
        with open(PIDFILE) as pid_file:
            pid = int(pid_file.read().strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(3)
        os.remove(PIDFILE)
    except FileNotFoundError:
        pass


def launch_vm(args) -> int:

    print("Starting TDX VM...")

    # Host-readiness gate (before profile resolution): the host QEMU build feeds
    # the guest ACPI tables measured into RTMR0, so an unbaselined QEMU would
    # attest with an RTMR0 we have no measurement for. Fail loud and early.
    verify_host_qemu_supported()

    mem = DEFAULT_MEM
    vcpus = DEFAULT_VCPUS
    smp_topology = f"{DEFAULT_VCPUS},sockets=1,cores={DEFAULT_VCPUS},threads=1"
    profile = None
    gpus = []

    if args.pass_gpus:
        profile = detect_profile()
        gpus = get_gpu_bdfs() or detect_nvidia_gpus()
        total_gpus = len(gpus)
        # Guest RAM is a FIXED, profile-determined value: it shapes the guest
        # ACPI/memory-map tables and therefore the TDX measurements, so it must
        # be identical across every host running this profile. We never resize
        # it to the host. We do refuse to launch if it cannot physically fit:
        # TDX guest memory is pinned and unreclaimable, so an over-large guest
        # OOM-kills the host instead of paging. Aborting is measurement-safe —
        # it never changes the VM.
        mem_gb = total_gpus * profile.ram_per_gpu_gb
        host_gb = detect_host_mem_gb()
        safe_gb = safe_vm_mem_gb(mem_gb, host_gb) if host_gb is not None else mem_gb
        if safe_gb < mem_gb:
            print(
                f"Error: profile '{profile.name}' needs {mem_gb}G guest RAM, but only "
                f"{safe_gb}G can be safely backed on this {host_gb}G host after reserving "
                f"headroom for the host OS, TDX PAMT, page tables, and VFIO pinning. "
                f"(TDX guest memory is pinned and unreclaimable, so an over-large guest "
                f"OOM-kills the host instead of paging.) Guest RAM is fixed for measurement "
                f"determinism and is never resized, so this host cannot run '{profile.name}'.",
                file=sys.stderr,
            )
            return 1
        mem = f"{mem_gb}G"
        vcpus = str(profile.vcpus)
        smp_topology = profile.smp_topology
        print(
            f"  GPU passthrough: {total_gpus}x {profile.name}"
            f" ({profile.vram_gb}GB VRAM each)"
            f" → {vcpus} vCPUs, {mem} RAM"
        )

    profile_wants_numa = profile is not None and profile.enable_numa_topology
    numa_active = use_numa_topology(profile_wants_numa)

    print(f"Launching TDX VM: {vcpus} vCPUs, {mem} RAM")
    print(f"Image: {args.image}")

    ubuntu_version = platform.freedesktop_os_release().get("VERSION_ID")
    cpu_args = "host" if ubuntu_version == "24.04" else "host,-avx10"

    pci_pinning = PcieRootPinning(numa_active)

    firmware_filename = profile.firmware_filename if profile else _DEFAULT_FIRMWARE
    firmware = _firmware_path(firmware_filename)
    print(f"Firmware: {firmware}")

    qemu_cmds = build_base_cmd(
        mem=mem,
        smp_topology=smp_topology,
        process_name=PROCESS_NAME,
        cpu_args=cpu_args,
        firmware=firmware,
        img_path=args.image,
        foreground=args.foreground,
        pidfile=PIDFILE,
        logfile=LOGFILE,
        enable_numa_topology=profile_wants_numa,
        pci_pinning=pci_pinning,
    )

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
        setup_passthrough(qemu_cmds)

    # Guest NUMA topology (numa_active) binds memory per node via QEMU
    # memory-backends, so no numactl prefix is needed. Otherwise interleave
    # across the GPUs' host NUMA nodes (all nodes if detection finds none).
    if numa_active:
        launch_prefix = []
    else:
        numa_nodes = detect_gpu_numa_nodes(gpus) if args.pass_gpus and gpus else []
        if numa_nodes:
            interleave = ",".join(str(n) for n in numa_nodes)
            print(f"  NUMA: interleaving memory across GPU nodes {interleave}")
        else:
            interleave = "all"
        launch_prefix = ["numactl", f"--interleave={interleave}"]

    print("Launching QEMU...")
    result = subprocess.run(
        launch_prefix + qemu_cmds,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        print(f"Error: QEMU failed (exit {result.returncode}).", file=sys.stderr)
        return result.returncode

    if not args.foreground:
        # vCPU thread pinning is gated on the profile enabling NUMA topology
        # (requires dual-socket host with PXB-PCIe grouping active). Host-wide
        # CPU power tuning is separate and operator-driven; see
        # `python -m chutes.host.tune` (tune-host.sh / restore-host.sh).
        pin_threads = numa_active and profile is not None and profile.enable_post_launch_tuning
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch a TDX VM with GPU passthrough")

    parser.add_argument("--image", type=str, help="Path to VM image")
    parser.add_argument("--pass-gpus", action="store_true")
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--ssh", action="store_true", help="Show SSH login hint after launch (benchmark and debug modes)")

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

    args = parser.parse_args()

    try:
        stop_existing_vm()
    except Exception:
        pass

    if args.clean:
        return 0

    if not args.image:
        print("Error: --image is required")
        return 1

    return launch_vm(args)


if __name__ == "__main__":
    sys.exit(main())
