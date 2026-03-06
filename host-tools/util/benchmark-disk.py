#!/usr/bin/env python3
"""
Disk benchmark script for TEE vs non-TEE comparison.

Tests:
- Sequential write (direct)
- Sequential write (buffered)
- fio sequential write
- fio random 4k write

Now includes:
- /tmp by default
- Filesystem type detection (ext4 vs tmpfs)

Usage:
  python benchmark-disk.py
  MOUNTS="/ /var/snap /cache/storage /tmp" python benchmark-disk.py
"""

import os
import subprocess
import time
import resource
import psutil
from pathlib import Path

# Added /tmp to defaults
MOUNTS = os.environ.get(
    "MOUNTS",
    "/ /var/snap /cache/storage /tmp"
).split()

TEST_SIZE_GB = int(os.environ.get("TEST_SIZE_GB", "8"))
FIO_RUNTIME = int(os.environ.get("FIO_RUNTIME", "30"))


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def drop_caches():
    run("sync")
    run("echo 3 > /proc/sys/vm/drop_caches")


def get_fs_type(path):
    r = run(f"findmnt -n -o FSTYPE --target {path}")
    return r.stdout.strip() or "unknown"


def dd_test(path, direct):
    fname = Path(path) / "diskbench.tmp"
    flags = "oflag=direct" if direct else ""
    cmd = f"dd if=/dev/zero of={fname} bs=1G count={TEST_SIZE_GB} {flags} status=none"

    start = time.perf_counter()
    start_pf = resource.getrusage(resource.RUSAGE_SELF)

    run(cmd)
    run("sync")

    end = time.perf_counter()
    end_pf = resource.getrusage(resource.RUSAGE_SELF)

    if fname.exists():
        fname.unlink()

    elapsed = end - start
    mbps = (TEST_SIZE_GB * 1024) / elapsed

    return {
        "elapsed": elapsed,
        "mbps": mbps,
        "minor_pf": end_pf.ru_minflt - start_pf.ru_minflt,
        "major_pf": end_pf.ru_majflt - start_pf.ru_majflt,
    }


def fio_test(path, mode, bs="1M", size="20G", iodepth=32, numjobs=1):
    fname = Path(path) / "fio.tmp"

    fio_cmd = f"""
    fio --name=test --filename={fname} \
        --size={size} --rw={mode} --bs={bs} \
        --ioengine=libaio --direct=1 \
        --iodepth={iodepth} --numjobs={numjobs} \
        --runtime={FIO_RUNTIME} --time_based \
        --group_reporting --output-format=terse
    """

    r = run(fio_cmd)

    if fname.exists():
        fname.unlink()

    try:
        parts = r.stdout.strip().split(";")
        bw_kbps = float(parts[47])  # write bandwidth KB/s
        iops = float(parts[48])     # write IOPS
        return {
            "bw_MBps": bw_kbps / 1024,
            "iops": iops,
        }
    except (IndexError, ValueError):
        return {"bw_MBps": None, "iops": None}


def print_section(title):
    print("=" * 60)
    print(title)
    print("=" * 60)


def benchmark_mount(path):
    print_section(f"Benchmarking {path}")
    print(f"Filesystem: {get_fs_type(path)}")

    if not os.path.exists(path):
        print("Mount not found.\n")
        return

    drop_caches()

    print("Sequential WRITE (direct)")
    r1 = dd_test(path, direct=True)
    print(f"  {r1['mbps']:.2f} MB/s  | minor_pf={r1['minor_pf']} major_pf={r1['major_pf']}")

    drop_caches()

    print("Sequential WRITE (buffered)")
    r2 = dd_test(path, direct=False)
    print(f"  {r2['mbps']:.2f} MB/s  | minor_pf={r2['minor_pf']} major_pf={r2['major_pf']}")

    drop_caches()

    print("fio SEQWRITE (1M)")
    r3 = fio_test(path, "write", bs="1M", size="20G", iodepth=32, numjobs=1)
    print(f"  {r3['bw_MBps']} MB/s  | {r3['iops']} IOPS")

    drop_caches()

    print("fio RANDWRITE (4k)")
    r4 = fio_test(path, "randwrite", bs="4k", size="10G", iodepth=64, numjobs=4)
    print(f"  {r4['bw_MBps']} MB/s  | {r4['iops']} IOPS")

    print()


def main():
    print_section("Disk Benchmark Starting")
    print(f"Mounts: {MOUNTS}")
    print(f"Test size: {TEST_SIZE_GB} GB")
    print(f"FIO runtime: {FIO_RUNTIME}s")

    for m in MOUNTS:
        benchmark_mount(m)


if __name__ == "__main__":
    main()