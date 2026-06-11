#!/usr/bin/env python3
"""
Benchmark HuggingFace download configurations inside a TDX VM.

Tests multiple download strategies to find the best balance of speed vs.
resource usage (thread/process count) in a constrained TDX guest.

Usage:
  python benchmark-hf-downloads.py meta-llama/Llama-3.3-70B-Instruct
  python benchmark-hf-downloads.py deepseek-ai/DeepSeek-R1-0528 --pattern "*.safetensors"
  python benchmark-hf-downloads.py microsoft/phi-2 --timeout 300

Requirements:
  pip install huggingface_hub   (v1.x -- also installs hf_xet)
  Optional system tools: aria2c, wget, curl (for raw HTTP baseline)

Each configuration downloads the largest file matching --pattern, cleans up,
and the final table compares MB/s + peak thread count.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIGS = [
    {
        "label": "xet-default",
        "desc": "XET adaptive concurrency (default)",
        "env": {},
    },
    {
        "label": "xet-throttled-4",
        "desc": "XET fixed concurrency=4, tokio threads=4",
        "env": {
            "HF_XET_FIXED_DOWNLOAD_CONCURRENCY": "4",
            "TOKIO_WORKER_THREADS": "4",
        },
    },
    {
        "label": "xet-throttled-8",
        "desc": "XET fixed concurrency=8, tokio threads=8",
        "env": {
            "HF_XET_FIXED_DOWNLOAD_CONCURRENCY": "8",
            "TOKIO_WORKER_THREADS": "8",
        },
    },
    {
        "label": "xet-throttled-16",
        "desc": "XET fixed concurrency=16, tokio threads=8",
        "env": {
            "HF_XET_FIXED_DOWNLOAD_CONCURRENCY": "16",
            "TOKIO_WORKER_THREADS": "8",
        },
    },
    {
        "label": "xet-hp",
        "desc": "XET high-performance mode (max resources)",
        "env": {"HF_XET_HIGH_PERFORMANCE": "1"},
    },
]

# ─── Subprocess download script (injected as -c argument) ────────────────────
# Uses pipe-delimited stdout for structured results back to the parent.
_DOWNLOAD_SCRIPT = """\
import os, sys, time, threading

start = time.monotonic()
peak_threads = threading.active_count()

def _monitor():
    global peak_threads
    while True:
        n = threading.active_count()
        if n > peak_threads:
            peak_threads = n
        time.sleep(0.5)

threading.Thread(target=_monitor, daemon=True).start()

try:
    from huggingface_hub import hf_hub_download
    p = hf_hub_download(
        repo_id="{repo_id}",
        filename="{filename}",
        revision="main",
        cache_dir=os.environ["HF_HOME"],
    )
    elapsed = time.monotonic() - start
    size = os.path.getsize(p)
    mbps = (size / 1048576) / elapsed if elapsed > 0 else 0
    print(f"OK|{{elapsed:.2f}}|{{size}}|{{mbps:.2f}}|{{peak_threads}}")
except Exception as e:
    elapsed = time.monotonic() - start
    print(f"FAIL|{{elapsed:.2f}}|0|0|0|{{e}}")
"""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def fmt_bytes(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.2f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.2f} MB"
    return f"{n / 1024:.1f} KB"


def resolve_test_file(repo_id: str, pattern: str, token: str) -> tuple[str, int]:
    """Find the largest file matching *pattern* in a HF repo."""
    url = f"https://huggingface.co/api/models/{repo_id}/tree/main"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HF API error {e.code} for {url}: {e.reason}")
    except urllib.error.URLError as e:
        sys.exit(f"Failed to reach HF API: {e.reason}")

    files = [
        (f["path"], f.get("size", 0))
        for f in data
        if f.get("type") == "file" and fnmatch.fnmatch(f["path"], pattern)
    ]
    if not files:
        sys.exit(f"No files matching '{pattern}' in {repo_id}")

    files.sort(key=lambda x: x[1], reverse=True)
    return files[0]


def print_system_info() -> None:
    print("System info:")
    print(f"  CPUs:     {os.cpu_count()}")
    try:
        import shutil as _s
        mem_line = ""
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    mem_line = fmt_bytes(kb * 1024)
                    break
        print(f"  Memory:   {mem_line}")
    except Exception:
        print("  Memory:   ?")
    print(f"  Kernel:   {platform.release()}")
    print(f"  Python:   {platform.python_version()}")

    for pkg, import_name in [("hf_hub", "huggingface_hub"), ("hf_xet", "hf_xet")]:
        try:
            mod = __import__(import_name)
            ver = getattr(mod, "__version__", "installed (no version attr)")
            print(f"  {pkg:8s}  {ver}")
        except ImportError:
            print(f"  {pkg:8s}  not installed")

    for tool in ("aria2c", "wget", "curl"):
        path = shutil.which(tool)
        print(f"  {tool:8s}  {path or 'not found'}")


# ─── Download runners ────────────────────────────────────────────────────────

def run_hf_download(
    repo_id: str,
    filename: str,
    cache_dir: str,
    env_overrides: dict[str, str],
    token: str,
    timeout: int,
) -> dict:
    """Run a single HF download in an isolated subprocess."""
    env = os.environ.copy()
    env["HF_HOME"] = cache_dir
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    if token:
        env["HF_TOKEN"] = token
    for k, v in env_overrides.items():
        env[k] = v
    if "HF_HUB_DISABLE_XET" not in env_overrides:
        env.pop("HF_HUB_DISABLE_XET", None)
    env.pop("HF_HUB_ENABLE_HF_TRANSFER", None)

    script = _DOWNLOAD_SCRIPT.format(repo_id=repo_id, filename=filename)

    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        output = proc.stdout.strip().split("\n")[-1] if proc.stdout.strip() else ""
        stderr = proc.stderr.strip()

        if not output:
            return {"status": "FAIL", "error": stderr[:200] or "no output", "elapsed": 0}

        parts = output.split("|")
        try:
            if parts[0] == "OK" and len(parts) >= 5:
                return {
                    "status": "OK",
                    "elapsed": float(parts[1]),
                    "bytes": int(parts[2]),
                    "mbps": float(parts[3]),
                    "peak_threads": int(parts[4]),
                }
            if parts[0] == "FAIL" and len(parts) >= 2:
                return {
                    "status": "FAIL",
                    "elapsed": float(parts[1]) if len(parts) > 1 else 0,
                    "error": "|".join(parts[5:]) if len(parts) > 5 else stderr[:200],
                    "peak_threads": int(parts[4]) if len(parts) > 4 else 0,
                }
        except (ValueError, IndexError):
            pass
        return {"status": "FAIL", "elapsed": 0, "error": stderr[:200] or output[:200]}
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "elapsed": timeout, "error": f"exceeded {timeout}s"}
    except Exception as e:
        return {"status": "FAIL", "elapsed": 0, "error": str(e)[:200]}


def run_raw_download(url: str, dest: Path, timeout: int, token: str = "") -> dict:
    """Download via aria2c / wget / curl as a network baseline."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    auth_header = f"Authorization: Bearer {token}" if token else ""

    if shutil.which("aria2c"):
        tool = "aria2c"
        cmd = [
            "aria2c", "-x", "16", "-s", "16", "-k", "1M",
            "--allow-overwrite=true",
            "-d", str(dest.parent), "-o", dest.name,
        ]
        if auth_header:
            cmd += ["--header", auth_header]
        cmd.append(url)
    elif shutil.which("wget"):
        tool = "wget"
        cmd = ["wget", "-q", "-O", str(dest)]
        if auth_header:
            cmd += ["--header", auth_header]
        cmd.append(url)
    elif shutil.which("curl"):
        tool = "curl"
        cmd = ["curl", "-sL", "-o", str(dest)]
        if auth_header:
            cmd += ["-H", auth_header]
        cmd.append(url)
    else:
        return {"status": "SKIP", "error": "no aria2c/wget/curl found"}

    start = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        elapsed = time.monotonic() - start
        if r.returncode != 0:
            return {
                "status": "FAIL",
                "elapsed": elapsed,
                "error": (r.stderr[:200] if r.stderr else f"rc={r.returncode}"),
            }
        actual_size = dest.stat().st_size if dest.exists() else 0
        mbps = (actual_size / 1048576) / elapsed if elapsed > 0 else 0
        return {
            "status": "OK",
            "elapsed": elapsed,
            "bytes": actual_size,
            "mbps": mbps,
            "tool": tool,
        }
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "elapsed": timeout, "error": f"exceeded {timeout}s"}


# ─── Output formatting ───────────────────────────────────────────────────────

SEP = "─" * 70
HEADER = f"{'LABEL':<22} {'STATUS':<8} {'TIME':>8} {'SPEED':>12} {'THREADS':>8}  DESCRIPTION"


def print_row(label: str, r: dict, desc: str) -> None:
    if r["status"] == "OK":
        threads = r.get("peak_threads", "N/A")
        print(f"{label:<22} {'OK':<8} {r['elapsed']:>7.1f}s {r['mbps']:>9.1f} MB/s {threads!s:>8}  {desc}")
    else:
        err_msg = r.get("error", "")
        if len(err_msg) > 40:
            err_msg = err_msg[:37] + "..."
        print(f"{label:<22} {r['status']:<8} {'':>8} {'':>12} {'':>8}  {err_msg}")


def print_summary(results: list[tuple[str, dict]]) -> None:
    print(f"\n{SEP}")
    print("SUMMARY")
    print(SEP)

    succeeded = [(l, r) for l, r in results if r.get("status") == "OK"]
    if not succeeded:
        print("  No successful downloads!")
        return

    best_label, _ = max(succeeded, key=lambda x: x[1]["mbps"])

    for label, r in succeeded:
        marker = " <-- FASTEST" if label == best_label else ""
        print(f"  {label:<22} {r['mbps']:>9.1f} MB/s  ({r['elapsed']:.1f}s){marker}")

    print()
    print("Recommendation:")
    if best_label.startswith("xet-throttled"):
        cfg = next(c for c in CONFIGS if c["label"] == best_label)
        env_str = " ".join(f"{k}={v}" for k, v in cfg["env"].items())
        print(f"  Throttled XET wins. Set: {env_str}")
        print("  Add these to system-manager.env.j2 and OPA allowed_env_names.")
    elif best_label == "xet-default":
        print("  Default XET works fine. You can remove HF_HUB_DISABLE_XET=1.")
    elif best_label == "xet-hp":
        print("  XET high-performance mode works. Set HF_XET_HIGH_PERFORMANCE=1.")
    print()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Benchmark HuggingFace download configurations in TDX",
    )
    ap.add_argument("repo_id", help="HF repo, e.g. deepseek-ai/DeepSeek-R1-0528")
    ap.add_argument("--pattern", default="*.safetensors",
                    help="Glob for selecting the test file (default: *.safetensors)")
    ap.add_argument("--timeout", type=int, default=600,
                    help="Per-download timeout in seconds (default: 600)")
    ap.add_argument("--dir", default=os.environ.get("BENCH_DIR", "/tmp/hf-bench"),
                    help="Working directory (default: /tmp/hf-bench)")
    ap.add_argument("--token", default=os.environ.get("HF_TOKEN", ""),
                    help="HuggingFace token (default: $HF_TOKEN)")
    args = ap.parse_args()

    token = args.token
    base = Path(args.dir)
    base.mkdir(parents=True, exist_ok=True)

    print(SEP)
    print("HuggingFace Download Benchmark for TDX")
    print(SEP)
    print(f"Repo:      {args.repo_id}")
    print(f"Pattern:   {args.pattern}")
    print(f"Timeout:   {args.timeout}s per config")
    print(f"WorkDir:   {base}")
    print(f"Token:     {'set (' + token[:8] + '...)' if token else 'NOT SET (gated models will fail)'}")
    print()

    print_system_info()
    print()

    # Resolve test file
    print("Resolving test file from HF API...")
    filename, file_size = resolve_test_file(args.repo_id, args.pattern, token)
    print(f"  {filename}  ({fmt_bytes(file_size)})")

    results: list[tuple[str, dict]] = []

    print(f"\n{SEP}")
    print(HEADER)
    print(SEP)

    # ── HF configurations ────────────────────────────────────────────
    for cfg in CONFIGS:
        label = cfg["label"]
        work = base / label
        cache = str(work / "cache")

        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(cache, exist_ok=True)

        r = run_hf_download(
            repo_id=args.repo_id,
            filename=filename,
            cache_dir=cache,
            env_overrides=cfg["env"],
            token=token,
            timeout=args.timeout,
        )
        print_row(label, r, cfg["desc"])
        results.append((label, r))

        shutil.rmtree(work, ignore_errors=True)

    # ── Summary ──────────────────────────────────────────────────────
    print_summary(results)


if __name__ == "__main__":
    main()
