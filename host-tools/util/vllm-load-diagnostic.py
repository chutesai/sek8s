#!/usr/bin/env python3
"""
vLLM Model Load Diagnostic and Benchmark Script

Diagnoses model load performance and GPU memory fragmentation when running vLLM
models in the Chutes environment. Mirrors the initialization process used by
the Chutes vLLM template (build_vllm_chute) so findings translate to production.

Reference: https://github.com/chutesai/chutes
- chutes/chute/template/vllm.py
- chutes/chute/template/helpers.py (set_default_cache_dirs, set_nccl_flags)
- chutes/image/standard/vllm.py

Environment parity with Chutes:
- HF_HOME, HF_HUB_CACHE, TRANSFORMERS_CACHE
- HF_HUB_DISABLE_XET, HF_HUB_ENABLE_HF_TRANSFER
- VLLM_WORKER_MULTIPROC_METHOD=spawn
- CUDA_DEVICE_COUNT, tensor_parallel_size
- Cache dirs: TRITON_CACHE_DIR, VLLM_CACHE_ROOT, etc.
- NCCL flags for H200/H100 (P2P, IB, GDR)

Usage:
  python vllm-load-diagnostic.py [options]
  python vllm-load-diagnostic.py --model Qwen/Qwen2.5-32B-Instruct --tensor-parallel 4
  python vllm-load-diagnostic.py --disable-cudagraph --spawn-second-instance
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import re
import resource
import subprocess
import sys
import time
from typing import Any

import psutil
import torch

# -----------------------------------------------------------------------------
# Chutes-compatible environment setup (must run before vLLM import)
# -----------------------------------------------------------------------------
DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-32B-Instruct"
DEFAULT_HF_HOME = "/var/snap/cache"

# Match Chutes template: https://github.com/chutesai/chutes/blob/main/chutes/chute/template/vllm.py
os.environ.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")

# Match sek8s/system-manager and benchmark-model.py
HF_HOME = os.environ.get("HF_HOME", DEFAULT_HF_HOME)
os.environ["HF_HOME"] = HF_HOME
os.environ.setdefault("HF_HUB_CACHE", f"{HF_HOME}/hub")
os.environ.setdefault("TRANSFORMERS_CACHE", f"{HF_HOME}/transformers")
# Chutes/sek8s system-manager uses these for faster downloads
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
os.environ.setdefault("VLLM_DISABLE_TELEMETRY", "1")

# Defer vLLM import until after env setup
from vllm import LLM, SamplingParams
from huggingface_hub import snapshot_download


# -----------------------------------------------------------------------------
# Chutes helpers (mirrored from chutes/chute/template/helpers.py)
# -----------------------------------------------------------------------------
def set_default_cache_dirs(download_path: str) -> None:
    """Mirror set_default_cache_dirs from Chutes helpers."""
    cache_keys = [
        "TRITON_CACHE_DIR",
        "TORCHINDUCTOR_CACHE_DIR",
        "FLASHINFER_WORKSPACE_BASE",
        "XFORMERS_CACHE_DIR",
        "DG_JIT_CACHE_DIR",
        "SGL_DG_CACHE_DIR",
        "SGLANG_DG_CACHE_DIR",
        "VLLM_CACHE_ROOT",
        "SGLANG_CACHE_DIR",
    ]
    for key in cache_keys:
        if not os.getenv(key):
            cache_dir = os.path.join(download_path, f"_{key.lower()}")
            os.environ[key] = cache_dir


def set_nccl_flags(gpu_count: int, gpu_model: str) -> None:
    """Mirror set_nccl_flags from Chutes - enables P2P/IB for H200/H100 etc."""
    if gpu_count > 1 and re.search(
        r"h[12]0|b[23]00|5090|l40s|6000 ada|a100|h800|pro 6000|sxm",
        gpu_model,
        re.I,
    ):
        for key in ["NCCL_P2P_DISABLE", "NCCL_IB_DISABLE", "NCCL_NET_GDR_LEVEL"]:
            if key in os.environ:
                del os.environ[key]


# -----------------------------------------------------------------------------
# Diagnostics: GPU memory
# -----------------------------------------------------------------------------
def get_gpu_memory_snapshot() -> dict[int, dict[str, Any]]:
    """Per-GPU memory stats via nvidia-smi and torch.cuda."""
    result = {}
    try:
        nv = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if nv.returncode == 0:
            for line in nv.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(", ")]
                if len(parts) >= 4:
                    idx = int(parts[0])
                    result[idx] = {
                        "total_mb": int(parts[1]),
                        "used_mb": int(parts[2]),
                        "free_mb": int(parts[3]),
                    }
    except Exception:
        pass

    # Augment with torch.cuda where available
    if torch.cuda.is_initialized():
        for i in range(torch.cuda.device_count()):
            if i not in result:
                result[i] = {"total_mb": 0, "used_mb": 0, "free_mb": 0}
            try:
                if hasattr(torch.cuda, "mem_get_info"):
                    free, total = torch.cuda.mem_get_info(i)
                    result[i]["mem_get_info_free"] = free
                    result[i]["mem_get_info_total"] = total
                    result[i]["largest_block_mb"] = (free // (1024 * 1024)) if free else None
                else:
                    result[i]["mem_get_info_free"] = None
                    result[i]["mem_get_info_total"] = None
                    result[i]["largest_block_mb"] = None
            except Exception:
                result[i]["mem_get_info_free"] = None
                result[i]["mem_get_info_total"] = None
                result[i]["largest_block_mb"] = None
            result[i]["allocated"] = torch.cuda.memory_allocated(i)
            result[i]["reserved"] = torch.cuda.memory_reserved(i)
            result[i]["max_allocated"] = torch.cuda.max_memory_allocated(i)

    return result


def get_torch_memory_stats() -> dict[str, Any]:
    """PyTorch allocator stats for fragmentation analysis."""
    if not torch.cuda.is_initialized():
        return {}
    try:
        stats = torch.cuda.memory_stats()
        return dict(stats)
    except Exception:
        return {}


def compute_fragmentation_metrics(stats: dict) -> dict[str, Any]:
    """Extract fragmentation indicators from torch.cuda.memory_stats()."""
    out = {}
    active = stats.get("active_bytes.all.current", 0) or stats.get("active", 0)
    reserved = stats.get("reserved_bytes.all.current", 0) or stats.get("reserved", 0)
    inactive_split = stats.get("inactive_split_bytes.all.current", 0) or stats.get(
        "inactive_split_bytes", 0
    )
    segment = stats.get("segment_bytes.all.current", 0) or stats.get("segment_bytes", 0)

    if active > 0:
        out["fragmentation_ratio"] = reserved / active
    else:
        out["fragmentation_ratio"] = 0.0
    out["active_bytes"] = active
    out["reserved_bytes"] = reserved
    out["inactive_split_bytes"] = inactive_split
    out["segment_bytes"] = segment
    out["fragmentation_likely"] = (
        out["fragmentation_ratio"] > 1.2 or inactive_split > 1024 * 1024 * 1024
    )
    return out


def print_gpu_snapshot(
    snap: dict[int, dict], stage: str, frag_metrics: dict | None = None
) -> None:
    """Print formatted GPU memory snapshot."""
    print()
    print("=" * 60)
    print(f"GPU MEMORY SNAPSHOT — {stage}")
    print("=" * 60)
    for idx in sorted(snap.keys()):
        s = snap[idx]
        total_gb = s.get("total_mb", 0) / 1024
        used_gb = s.get("used_mb", 0) / 1024
        free_gb = s.get("free_mb", 0) / 1024
        lb = s.get("largest_block_mb")
        lb_str = f"{lb / 1024:.1f} GB" if lb is not None else "N/A"
        res = s.get("reserved", 0) / (1024**3)
        alloc = s.get("allocated", 0) / (1024**3)
        frag = "HIGH" if (frag_metrics or {}).get("fragmentation_likely") else "OK"
        print(f"\nGPU {idx}")
        print(f"  Total:          {total_gb:.1f} GB")
        print(f"  Used:           {used_gb:.1f} GB")
        print(f"  Free:           {free_gb:.1f} GB")
        print(f"  Largest block:  {lb_str}")
        if "reserved" in s:
            print(f"  Reserved:       {res:.1f} GB")
        if "allocated" in s:
            print(f"  Allocated:      {alloc:.1f} GB")
        print(f"  Fragmentation:  {frag}")
    if frag_metrics:
        print("\nFragmentation metrics:")
        print(f"  ratio (reserved/active): {frag_metrics.get('fragmentation_ratio', 0):.2f}")
        print(f"  inactive_split: {frag_metrics.get('inactive_split_bytes', 0) / 1024**3:.2f} GB")
        if frag_metrics.get("fragmentation_likely"):
            print("  ⚠️  Fragmentation likely contributing to OOM risk")


# -----------------------------------------------------------------------------
# Diagnostics: Disk and page cache
# -----------------------------------------------------------------------------
def get_disk_io() -> dict[str, int]:
    """Disk IO counters."""
    try:
        io = psutil.disk_io_counters()
        return {"read_bytes": io.read_bytes, "write_bytes": io.write_bytes}
    except Exception:
        return {"read_bytes": 0, "write_bytes": 0}


def get_page_faults() -> tuple[int, int]:
    """Minor and major page faults."""
    try:
        r = resource.getrusage(resource.RUSAGE_SELF)
        return r.ru_minflt, r.ru_majflt
    except Exception:
        return 0, 0


def get_mount_usage(path: str) -> dict[str, float]:
    """Disk usage for a path (e.g. HF_HOME)."""
    try:
        u = psutil.disk_usage(path)
        return {"used_gb": u.used / 1024**3, "free_gb": u.free / 1024**3}
    except Exception:
        return {"used_gb": 0, "free_gb": 0}


# -----------------------------------------------------------------------------
# Diagnostics: NCCL and topology
# -----------------------------------------------------------------------------
def run_nvidia_commands() -> dict[str, str]:
    """Capture nvidia-smi topo, nvlink, and env vars."""
    out = {}
    for name, cmd in [
        ("topo", ["nvidia-smi", "topo", "-m"]),
        ("nvlink", ["nvidia-smi", "nvlink", "-s"]),
        ("gpu_query", ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,memory.free", "--format=csv"]),
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            out[name] = r.stdout if r.returncode == 0 else r.stderr or "(failed)"
        except Exception as e:
            out[name] = str(e)
    return out


def get_relevant_env() -> dict[str, str]:
    """NCCL, CUDA, VLLM, PYTORCH_CUDA_ALLOC_CONF."""
    keys = []
    for prefix in ["NCCL_", "CUDA_", "VLLM_", "PYTORCH_CUDA_ALLOC_CONF"]:
        keys.extend(k for k in os.environ if k.startswith(prefix) or k == prefix)
    return {k: os.environ.get(k, "") for k in sorted(set(keys))}


# -----------------------------------------------------------------------------
# Stage timing and load flow
# -----------------------------------------------------------------------------
def run_diagnostic(
    model_name: str,
    revision: str | None,
    tensor_parallel: int,
    hf_home: str,
    disable_cudagraph: bool,
    cudagraph_sizes: list[int] | None,
    warmup_tokens: int,
) -> dict[str, Any]:
    """Run full diagnostic load with timing and memory snapshots."""
    proc = psutil.Process(os.getpid())
    timeline = {}
    snapshots = {}
    disk_samples = []
    pf_samples = []

    # --- Python startup (implicit: time from script start to here) ---
    t0 = time.perf_counter()

    # --- CUDA init (Chutes does this before model load) ---
    torch.cuda.empty_cache()
    torch.cuda.init()
    if torch.cuda.device_count() == 0:
        raise RuntimeError("No CUDA devices available")
    torch.cuda.set_device(0)
    multiprocessing.set_start_method("spawn", force=True)

    gpu_count = int(os.environ.get("CUDA_DEVICE_COUNT", str(torch.cuda.device_count())))
    if tensor_parallel > 0:
        gpu_count = min(gpu_count, tensor_parallel)
    gpu_model = torch.cuda.get_device_name(0)
    set_nccl_flags(gpu_count, gpu_model)

    t1 = time.perf_counter()
    timeline["python_startup_cuda_init"] = t1 - t0

    # --- Snapshot 0: before model load ---
    snapshots["before_load"] = get_gpu_memory_snapshot()
    disk_samples.append(get_disk_io())
    pf_samples.append(get_page_faults())
    mount_before = get_mount_usage(hf_home)

    # --- HF snapshot resolution ---
    t2 = time.perf_counter()
    download_kwargs = {}
    if revision:
        download_kwargs["revision"] = revision
    download_path = snapshot_download(model_name, **download_kwargs)
    torch.cuda.synchronize()
    t3 = time.perf_counter()
    timeline["hf_snapshot_resolve"] = t3 - t2

    set_default_cache_dirs(download_path)

    # --- Build LLM kwargs (Chutes parity) ---
    llm_kwargs: dict[str, Any] = {
        "model": model_name,
        "tensor_parallel_size": gpu_count,
        "trust_remote_code": True,
    }
    if revision:
        llm_kwargs["revision"] = revision

    if disable_cudagraph:
        # vLLM: disable CUDA graphs for isolation (matches --disable-cudagraph)
        try:
            from vllm.config import CompilationConfig
            try:
                from vllm.config import CudagraphMode
                mode = CudagraphMode.NONE
            except (ImportError, AttributeError):
                mode = "NONE"
            llm_kwargs["compilation_config"] = CompilationConfig(cudagraph_mode=mode)
        except (ImportError, AttributeError, TypeError):
            llm_kwargs["enable_cuda_graph"] = False
    if cudagraph_sizes:
        try:
            from vllm.config import CompilationConfig
            cfg = llm_kwargs.get("compilation_config")
            if cfg is None:
                cfg = CompilationConfig()
            if hasattr(cfg, "cudagraph_capture_sizes"):
                cfg.cudagraph_capture_sizes = cudagraph_sizes
            llm_kwargs["compilation_config"] = cfg
        except Exception:
            pass

    # --- Full model load (disk reads + host tensors + GPU copy + KV cache + cuda graph) ---
    disk_before = get_disk_io()
    pf_before = get_page_faults()
    t4 = time.perf_counter()

    llm = LLM(**llm_kwargs)

    torch.cuda.synchronize()
    t5 = time.perf_counter()
    timeline["model_load_total"] = t5 - t4

    disk_after = get_disk_io()
    pf_after = get_page_faults()
    timeline["disk_read_bytes"] = disk_after["read_bytes"] - disk_before["read_bytes"]
    timeline["disk_write_bytes"] = disk_after["write_bytes"] - disk_before["write_bytes"]
    timeline["minor_flt"] = pf_after[0] - pf_before[0]
    timeline["major_flt"] = pf_after[1] - pf_before[1]

    # --- Snapshot 1: after weights load ---
    snapshots["after_load"] = get_gpu_memory_snapshot()
    frag_after_load = compute_fragmentation_metrics(get_torch_memory_stats())

    # --- Warmup inference ---
    t6 = time.perf_counter()
    sampling_params = SamplingParams(max_tokens=warmup_tokens)
    _ = llm.generate(["Hello, world!"], sampling_params=sampling_params)
    torch.cuda.synchronize()
    t7 = time.perf_counter()
    timeline["warmup_inference"] = t7 - t6

    # --- Snapshot 2: after warmup ---
    snapshots["after_warmup"] = get_gpu_memory_snapshot()
    frag_after_warmup = compute_fragmentation_metrics(get_torch_memory_stats())

    # --- Totals ---
    vram_delta = 0
    for i, s in snapshots["after_load"].items():
        b = snapshots["before_load"].get(i, {})
        used_after = s.get("used_mb", 0) * 1024 * 1024
        used_before = b.get("used_mb", 0) * 1024 * 1024
        vram_delta += used_after - used_before

    timeline["total_time"] = t7 - t0
    timeline["vram_loaded_bytes"] = vram_delta
    timeline["snapshots"] = snapshots
    timeline["frag_after_load"] = frag_after_load
    timeline["frag_after_warmup"] = frag_after_warmup
    timeline["rss_final"] = proc.memory_info().rss
    mount_after = get_mount_usage(hf_home)
    timeline["mount_used_delta"] = mount_after["used_gb"] - mount_before["used_gb"]

    return timeline


# -----------------------------------------------------------------------------
# Multi-instance simulation
# -----------------------------------------------------------------------------
def run_second_instance(
    model_name: str,
    revision: str | None,
    tensor_parallel: int,
    gpu_offset: int,
) -> dict[str, Any]:
    """
    Launch second model instance on a different GPU set (CUDA_VISIBLE_DEVICES).
    Reproduces OOM when second instance fails during CUDA graph capture.
    """
    gpu_count = torch.cuda.device_count()
    if gpu_offset + tensor_parallel > gpu_count:
        return {"error": f"Not enough GPUs: need {gpu_offset + tensor_parallel}, have {gpu_count}"}

    devices = ",".join(str(i) for i in range(gpu_offset, gpu_offset + tensor_parallel))
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = devices
    # Remap so second instance sees 0,1,2,...
    env["CUDA_DEVICE_COUNT"] = str(tensor_parallel)

    cmd = [
        sys.executable,
        __file__,
        "--model", model_name,
        "--tensor-parallel", str(tensor_parallel),
        "--no-print-env",
        "--no-print-topology",
    ]
    if revision:
        cmd.extend(["--revision", revision])

    try:
        r = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        return {
            "success": r.returncode == 0,
            "returncode": r.returncode,
            "stdout": r.stdout[-5000:] if len(r.stdout) > 5000 else r.stdout,
            "stderr": r.stderr[-5000:] if len(r.stderr) > 5000 else r.stderr,
        }
    except subprocess.TimeoutExpired as e:
        return {"success": False, "error": "timeout", "stderr": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# -----------------------------------------------------------------------------
# Output formatting
# -----------------------------------------------------------------------------
def print_timeline(t: dict) -> None:
    """Print MODEL LOAD TIMELINE section."""
    print()
    print("MODEL LOAD TIMELINE")
    print("-" * 40)
    for k, v in [
        ("Python startup + CUDA init", t.get("python_startup_cuda_init", 0)),
        ("HF snapshot resolve", t.get("hf_snapshot_resolve", 0)),
        ("Model load (weights+KV+cudagraph)", t.get("model_load_total", 0)),
        ("Warmup inference", t.get("warmup_inference", 0)),
        ("TOTAL", t.get("total_time", 0)),
    ]:
        print(f"{k:35} {v:6.1f}s")
    print()


def print_disk_pcie(t: dict) -> None:
    """Print disk and PCIe throughput."""
    load_time = t.get("model_load_total") or 1
    vram_gb = t.get("vram_loaded_bytes", 0) / 1024**3
    disk_read_gb = t.get("disk_read_bytes", 0) / 1024**3
    disk_write_gb = t.get("disk_write_bytes", 0) / 1024**3

    print("DISK & PCIe DIAGNOSTICS")
    print("-" * 40)
    print(f"Disk read:         {disk_read_gb:.2f} GB")
    print(f"Disk write:        {disk_write_gb:.2f} GB")
    print(f"HF cache growth:   {t.get('mount_used_delta', 0):.2f} GB")
    print(f"Minor page faults: {t.get('minor_flt', 0):,}")
    print(f"Major page faults: {t.get('major_flt', 0):,}")
    print(f"Effective disk throughput:  {disk_read_gb / load_time:.2f} GB/s")
    print(f"Effective PCIe throughput:   {vram_gb / load_time:.2f} GB/s")
    if disk_read_gb < 0.1 and vram_gb > 1:
        print("  (likely from page cache; use 'echo 3 > /proc/sys/vm/drop_caches' for cold)")
    print()


def print_warnings(t: dict) -> None:
    """Warn if fragmentation likely caused OOM."""
    for stage, frag in [("after_load", t.get("frag_after_load")), ("after_warmup", t.get("frag_after_warmup"))]:
        if frag and frag.get("fragmentation_likely"):
            print(f"⚠️  HIGH FRAGMENTATION at {stage} — may cause OOM during CUDA graph capture")
            print(f"   fragmentation_ratio={frag.get('fragmentation_ratio', 0):.2f}")
    print()


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="vLLM load diagnostic for Chutes environment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--model", default=os.environ.get("MODEL_NAME", DEFAULT_MODEL))
    parser.add_argument("--revision", default=None)
    parser.add_argument("--hf-home", default=os.environ.get("HF_HOME", DEFAULT_HF_HOME))
    parser.add_argument("--tensor-parallel", "-tp", type=int, default=0)
    parser.add_argument("--disable-cudagraph", action="store_true")
    parser.add_argument("--cudagraph-sizes", type=str, default=None, help="Comma-separated batch sizes")
    parser.add_argument("--warmup-tokens", type=int, default=10)
    parser.add_argument("--spawn-second-instance", action="store_true")
    parser.add_argument("--no-print-env", action="store_true", help="Skip env dump (for subprocess)")
    parser.add_argument("--no-print-topology", action="store_true", help="Skip topo dump (for subprocess)")
    args = parser.parse_args()

    cudagraph_sizes = None
    if args.cudagraph_sizes:
        cudagraph_sizes = [int(x) for x in args.cudagraph_sizes.split(",")]

    # Header
    if not args.no_print_env:
        print("=" * 60)
        print("vLLM Load Diagnostic (Chutes parity)")
        print("=" * 60)
        print(f"Model:           {args.model}")
        print(f"Revision:        {args.revision or 'default'}")
        print(f"HF_HOME:         {args.hf_home}")
        print(f"Tensor parallel: {args.tensor_parallel or 'auto'}")
        print(f"Disable cudagraph: {args.disable_cudagraph}")
        print(f"GPUs:            {torch.cuda.device_count()}")
        print()

    if not args.no_print_topology:
        nv = run_nvidia_commands()
        print("NVIDIA TOPOLOGY")
        print("-" * 40)
        print(nv.get("topo", "N/A")[:2000])
        print()
        print("ENVIRONMENT (NCCL, CUDA, VLLM)")
        print("-" * 40)
        for k, v in get_relevant_env().items():
            print(f"  {k}={v}")
        print()

    try:
        result = run_diagnostic(
            model_name=args.model,
            revision=args.revision,
            tensor_parallel=args.tensor_parallel,
            hf_home=args.hf_home,
            disable_cudagraph=args.disable_cudagraph,
            cudagraph_sizes=cudagraph_sizes,
            warmup_tokens=args.warmup_tokens,
        )
    except Exception as e:
        print(f"DIAGNOSTIC FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # Output
    print_timeline(result)
    print_disk_pcie(result)
    print_gpu_snapshot(
        result["snapshots"]["before_load"],
        "before load",
    )
    print_gpu_snapshot(
        result["snapshots"]["after_load"],
        "after load",
        result.get("frag_after_load"),
    )
    print_gpu_snapshot(
        result["snapshots"]["after_warmup"],
        "after warmup",
        result.get("frag_after_warmup"),
    )
    print_warnings(result)

    if args.spawn_second_instance:
        print("=" * 60)
        print("SECOND INSTANCE SIMULATION")
        print("=" * 60)
        tp = args.tensor_parallel or torch.cuda.device_count()
        second = run_second_instance(args.model, args.revision, tp, gpu_offset=tp)
        if "error" in second:
            print(f"Error: {second['error']}")
        else:
            print(f"Success: {second.get('success')}")
            print(f"Return code: {second.get('returncode')}")
            if not second.get("success") and second.get("stderr"):
                print("Stderr (last 2k):")
                print(second["stderr"][-2000:])
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
