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

from chutes_host.detection import detect_nvidia_gpus, get_gpu_bdfs, get_gpu_models_from_lspci
from chutes_host.gpu.profiles import resolve_profile
from chutes_host.passthrough import setup_passthrough
from chutes_host.qemu import add_volumes, add_vsock, build_base_cmd, build_network

PIDFILE = '/tmp/tdx-td-pid.pid'
LOGFILE = '/tmp/tdx-guest-td.log'
PROCESS_NAME = 'chutes-td'

DEFAULT_MEM = '100G'
DEFAULT_VCPUS = '32'

# TDVF MUST NOT be overridden (MRTD depends on it)
_FIRMWARE_REL = '../../firmware/TDVF.fd'


def _firmware_path() -> str:
    scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(scripts_dir, _FIRMWARE_REL)


def print_vm_status(ssh_port: int):
    try:
        with open(PIDFILE) as pid_file:
            pid = int(pid_file.read())
            print(f'TDX VM running with PID: {pid}')
            print(f'Login:')
            print(f'   ssh -p {ssh_port} tdx@localhost   (default: tdx/123456)')
            print(f'   ssh -p {ssh_port} root@localhost  (password: 123456)')
    except Exception:
        pass


def stop_existing_vm():
    print('Clean VM')
    try:
        with open(PIDFILE) as pid_file:
            pid = int(pid_file.read())
            os.kill(pid, signal.SIGTERM)
            time.sleep(3)
        os.remove(PIDFILE)
    except FileNotFoundError:
        pass


def launch_vm(args):
    mem = DEFAULT_MEM
    vcpus = DEFAULT_VCPUS

    # When passing GPUs, size RAM to match total VRAM (e.g. 8x H200 = 8*141G)
    if args.pass_gpus:
        gpus = get_gpu_bdfs()
        if not gpus:
            gpus = detect_nvidia_gpus()
        if gpus:
            gpu_models = get_gpu_models_from_lspci(gpus)
            profile = resolve_profile(gpu_models)
            total_gpus = len(gpus)
            mem = f"{total_gpus * profile.vram_gb}G"
            print(f'  GPU passthrough: {total_gpus}x {profile.name} ({profile.vram_gb}GB VRAM each) → {mem} RAM')

    print(f'Launching TDX VM: {vcpus} vCPUs, {mem} RAM')
    print(f'Image: {args.image}')

    ubuntu_version = platform.freedesktop_os_release().get('VERSION_ID')
    cpu_args = 'host' if ubuntu_version == '24.04' else 'host,-avx10'

    qemu_cmds = build_base_cmd(
        mem=mem,
        vcpus=vcpus,
        process_name=PROCESS_NAME,
        cpu_args=cpu_args,
        firmware=_firmware_path(),
        img_path=args.image,
        foreground=args.foreground,
        pidfile=PIDFILE,
        logfile=LOGFILE,
    )

    build_network(
        qemu_cmds,
        network_type=args.network_type,
        net_iface=args.net_iface,
        ssh_port=args.ssh_port,
        net_queues=args.net_queues,
    )

    add_volumes(
        qemu_cmds,
        config_volume=args.config_volume,
        cache_volume=args.cache_volume,
        storage_volume=args.storage_volume,
    )

    add_vsock(qemu_cmds)

    if args.pass_gpus:
        setup_passthrough(qemu_cmds)

    print('Launching QEMU...')
    subprocess.run(qemu_cmds, stderr=subprocess.STDOUT)

    if not args.foreground:
        print(f'Log file: {LOGFILE}')
    print_vm_status(args.ssh_port)


def main() -> int:
    parser = argparse.ArgumentParser(description='Launch a TDX VM with GPU passthrough')

    parser.add_argument("--image", type=str, help="Path to VM image")
    parser.add_argument("--pass-gpus", action='store_true')
    parser.add_argument("--foreground", action='store_true')
    parser.add_argument("--clean", action='store_true')

    parser.add_argument("--config-volume", type=str)
    parser.add_argument("--cache-volume", type=str)
    parser.add_argument("--storage-volume", type=str,
                        help="Storage volume for VM storage (containerd and kubelet-pods)")
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

    launch_vm(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
