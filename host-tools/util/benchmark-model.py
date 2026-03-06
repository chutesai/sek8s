#!/usr/bin/env python3
"""
TEE vs non-TEE full model load benchmark
Now includes disk IO statistics.

Defaults:
- MODEL_NAME = Qwen/Qwen2.5-Coder-32B-Instruct
- HF_HOME = /var/snap/cache
"""

import os
import resource
import subprocess
import sys
import time
import psutil
import torch
from vllm import LLM


# 🔹 Hard defaults (can still override via env if needed)
DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-32B-Instruct"
DEFAULT_HF_HOME = "/var/snap/cache"

MODEL_NAME = os.environ.get("MODEL_NAME", DEFAULT_MODEL)
HF_HOME = os.environ.get("HF_HOME", DEFAULT_HF_HOME)

os.environ["HF_HOME"] = HF_HOME
os.environ.setdefault("HF_HUB_CACHE", f"{HF_HOME}/hub")
os.environ.setdefault("TRANSFORMERS_CACHE", f"{HF_HOME}/transformers")


def _get_gpu_memory_used_mb():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        out = {}
        for line in result.stdout.strip().splitlines():
            idx, used_mb = line.split(", ")
            out[int(idx.strip())] = int(used_mb.strip())
        return out
    except Exception:
        return None


def _get_disk_io():
    io = psutil.disk_io_counters()
    return {
        "read_bytes": io.read_bytes,
        "write_bytes": io.write_bytes,
    }


def _get_mount_usage(path):
    usage = psutil.disk_usage(path)
    return {
        "used_gb": usage.used / 1024**3,
        "free_gb": usage.free / 1024**3,
    }


def _run_benchmark():
    gpu_count = torch.cuda.device_count()
    proc = psutil.Process(os.getpid())

    # ---- Baseline stats ----
    start_rss = proc.memory_info().rss
    start_pf = resource.getrusage(resource.RUSAGE_SELF)
    start_vram_mb = _get_gpu_memory_used_mb() or {}
    start_disk = _get_disk_io()
    start_mount = _get_mount_usage(HF_HOME)

    torch.cuda.synchronize()
    start_time = time.perf_counter()

    llm = LLM(
        model=MODEL_NAME,
        tensor_parallel_size=gpu_count,
        trust_remote_code=True,
    )

    torch.cuda.synchronize()
    end_time = time.perf_counter()

    # ---- Final stats ----
    end_rss = proc.memory_info().rss
    end_pf = resource.getrusage(resource.RUSAGE_SELF)
    end_vram_mb = _get_gpu_memory_used_mb() or {}
    end_disk = _get_disk_io()
    end_mount = _get_mount_usage(HF_HOME)

    # VRAM delta
    vram_per_gpu = {}
    for i in range(gpu_count):
        vram_per_gpu[i] = (
            (end_vram_mb.get(i, 0) - start_vram_mb.get(i, 0))
            * 1024 * 1024
        )
    total_vram = sum(vram_per_gpu.values())

    return {
        "load_time": end_time - start_time,
        "rss_delta": end_rss - start_rss,
        "rss_final": end_rss,
        "vram_delta": total_vram,
        "vram_per_gpu": vram_per_gpu,
        "minor_flt": end_pf.ru_minflt - start_pf.ru_minflt,
        "major_flt": end_pf.ru_majflt - start_pf.ru_majflt,
        "disk_read": end_disk["read_bytes"] - start_disk["read_bytes"],
        "disk_write": end_disk["write_bytes"] - start_disk["write_bytes"],
        "mount_used_delta":
            end_mount["used_gb"] - start_mount["used_gb"],
    }


def _print_results(r):
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)

    print(f"Model:              {MODEL_NAME}")
    print(f"HF_HOME:            {HF_HOME}")
    print(f"Load time:          {r['load_time']:.2f} sec")
    print()

    print(f"Host RSS delta:     {r['rss_delta']/1024**3:.2f} GB")
    print(f"Final RSS:          {r['rss_final']/1024**3:.2f} GB")
    print()

    print(f"Total VRAM loaded:  {r['vram_delta']/1024**3:.2f} GB")
    for i, delta in r["vram_per_gpu"].items():
        if delta > 0:
            print(f"  GPU {i}:          {delta/1024**3:.2f} GB")

    print()
    print(f"Minor page faults:  {r['minor_flt']:,}")
    print(f"Major page faults:  {r['major_flt']:,}")
    print()

    print("Disk IO during load:")
    print(f"  Read:             {r['disk_read']/1024**3:.2f} GB")
    print(f"  Write:            {r['disk_write']/1024**3:.2f} GB")
    print(f"  Cache growth:     {r['mount_used_delta']:.2f} GB")

    if r["load_time"] > 0:
        print()
        # PCIe throughput: rate data moved to GPU (always meaningful)
        pcie_gbps = (r["vram_delta"] / 1024**3) / r["load_time"]
        print(f"Effective PCIe throughput:    {pcie_gbps:.2f} GB/s")
        # Disk throughput: only meaningful when data comes from disk (cold load)
        disk_read_gb = r["disk_read"] / 1024**3
        disk_gbps = disk_read_gb / r["load_time"]
        print(f"Effective disk read throughput: {disk_gbps:.2f} GB/s", end="")
        if disk_read_gb < 0.1 and r["vram_delta"] > 1024**3:
            print("  (from page cache; use 'echo 3 > /proc/sys/vm/drop_caches' for cold)")
        else:
            print()


def main():
    print("=" * 60)
    print("Chutes Model Load Benchmark")
    print("=" * 60)
    print(f"Model:    {MODEL_NAME}")
    print(f"HF_HOME:  {HF_HOME}")
    print(f"GPUs:     {torch.cuda.device_count()}")
    print("=" * 60)

    r = _run_benchmark()
    _print_results(r)


if __name__ == "__main__":
    main()