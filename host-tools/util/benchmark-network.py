#!/usr/bin/env python3
"""
Network/download benchmark for TEE VM: compare download paths to isolate bottleneck.

Scenarios (use --scenarios to choose which to run):

  1. raw          Direct HTTP via aria2c (or wget). No Python HF library.
                  Baseline: measures raw network throughput.

  2. hf           huggingface_hub with default httpx backend (XET disabled).
                  Same path as chute cold-start model download.

  3. hf_fast     huggingface_hub + patch (no progress, 64MB chunks).
                  Tests if GIL/buffer size is the bottleneck.

All scenarios download the same N largest .safetensors files for a fair comparison.

Interpretation:
  - raw fast, hf slow        → TDX + Python/HF overhead
  - all slow                 → network/NAT bottleneck

Usage:
  # All three scenarios (default)
  python benchmark-network.py deepseek-ai/DeepSeek-R1-0528

  # Specific scenarios only
  python benchmark-network.py deepseek-ai/DeepSeek-R1-0528 --scenarios raw,hf

  # Fewer files for faster runs
  python benchmark-network.py deepseek-ai/DeepSeek-R1-0528 --files 3

Env:
  BENCH_DIR     base dir; each scenario uses a subdir (default: /tmp/network-bench)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCENARIOS = ("raw", "hf", "hf_fast")


def _apply_hf_performance_patch(chunk_size_mb: int = 64) -> None:
    """Patch huggingface_hub: disable progress bars (GIL contention) and larger chunks."""
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    try:
        from huggingface_hub import constants as hf_constants
        hf_constants.DOWNLOAD_CHUNK_SIZE = chunk_size_mb * 1024 * 1024
        hf_constants.HF_HUB_DISABLE_PROGRESS_BARS = True
    except ImportError:
        pass


def fmt_size(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.2f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.2f} MB"
    return f"{n / 1024:.1f} KB"


def fetch_hf_tree(repo_id: str, revision: str = "main") -> list[dict]:
    """Fetch file tree from HF API (stdlib only)."""
    parts = repo_id.split("/")
    if len(parts) != 2:
        raise ValueError(f"repo_id must be org/repo, got {repo_id}")
    org, repo = parts
    url = f"https://huggingface.co/api/models/{org}/{repo}/tree/{revision}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HF API error {e.code} for {url}: {e.reason}. Check repo_id and network.") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"Failed to fetch {url}: {e.reason}") from e


def get_largest_files(tree: list[dict], ext: str = ".safetensors", n: int = 5) -> list[tuple[str, int]]:
    """Return (path, size) for N largest files with given extension."""
    files = [(e["path"], e["size"]) for e in tree if e.get("type") == "file" and e["path"].endswith(ext)]
    files.sort(key=lambda x: x[1], reverse=True)
    return files[:n]


def build_direct_url(repo_id: str, path: str, revision: str = "main") -> str:
    return f"https://huggingface.co/{repo_id}/resolve/{revision}/{path}"


def run_raw_download(
    urls_with_sizes: list[tuple[str, int, str]],
    dest_dir: Path,
    use_aria2c: bool = False,
) -> dict:
    """Download via wget or aria2c. Returns {elapsed, bytes, mbps}."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    total_bytes = sum(s for _, s, _ in urls_with_sizes)

    if use_aria2c and shutil.which("aria2c"):
        # aria2c: multi-connection, often faster
        elapsed_total = 0.0
        for url, _, name in urls_with_sizes:
            start = time.perf_counter()
            r = subprocess.run(
                ["aria2c", "-x", "16", "-s", "16", "-k", "1M", "-d", str(dest_dir), "-o", name, url],
                capture_output=True,
                text=True,
                timeout=7200,
            )
            elapsed_total += time.perf_counter() - start
            if r.returncode != 0:
                return {"elapsed": elapsed_total, "bytes": 0, "mbps": 0, "error": r.stderr or r.stdout}
        mbps = (total_bytes / (1024 * 1024)) / elapsed_total if elapsed_total else 0
        return {"elapsed": elapsed_total, "bytes": total_bytes, "mbps": mbps}
    else:
        # wget
        start = time.perf_counter()
        for url, _, name in urls_with_sizes:
            out = dest_dir / name
            r = subprocess.run(
                ["wget", "-q", "-O", str(out), url],
                capture_output=True,
                text=True,
                timeout=7200,
            )
            if r.returncode != 0:
                return {
                    "elapsed": time.perf_counter() - start,
                    "bytes": 0,
                    "mbps": 0,
                    "error": r.stderr or r.stdout,
                }
        elapsed = time.perf_counter() - start
        mbps = (total_bytes / (1024 * 1024)) / elapsed if elapsed else 0
        return {"elapsed": elapsed, "bytes": total_bytes, "mbps": mbps}


def run_hf_download(
    repo_id: str,
    revision: str,
    cache_dir: Path,
    dest_dir: Path,
    files_only: list[tuple[str, int]] | None = None,
) -> dict:
    """Download via huggingface_hub (XET disabled, httpx backend)."""
    try:
        from huggingface_hub import hf_hub_download, snapshot_download
    except ImportError:
        return {"elapsed": 0, "bytes": 0, "mbps": 0, "error": "huggingface_hub not installed; pip install huggingface_hub"}

    os.environ["HF_HOME"] = str(cache_dir)
    os.environ["HF_HUB_DISABLE_XET"] = "1"

    dest_dir.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    total_bytes = 0

    try:
        if files_only:
            for path, _ in files_only:
                p = hf_hub_download(
                    repo_id=repo_id,
                    filename=path,
                    revision=revision,
                    cache_dir=cache_dir,
                )
                total_bytes += Path(p).stat().st_size
        else:
            path = snapshot_download(
                repo_id=repo_id,
                revision=revision,
                cache_dir=cache_dir,
            )
            total_bytes = sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())
    except Exception as e:
        return {
            "elapsed": time.perf_counter() - start,
            "bytes": 0,
            "mbps": 0,
            "error": str(e),
        }
    elapsed = time.perf_counter() - start
    mbps = (total_bytes / (1024 * 1024)) / elapsed if elapsed else 0
    return {"elapsed": elapsed, "bytes": total_bytes, "mbps": mbps}


def main():
    ap = argparse.ArgumentParser(description="Benchmark download scenarios in TEE VM")
    ap.add_argument("repo_id", help="HF repo, e.g. deepseek-ai/DeepSeek-R1-0528")
    ap.add_argument("--revision", default="main", help="Branch/tag/commit")
    ap.add_argument("--files", type=int, default=5, help="Number of largest .safetensors files")
    ap.add_argument(
        "--scenarios",
        default=",".join(SCENARIOS),
        help=f"Comma-separated: {', '.join(SCENARIOS)} (default: all)",
    )
    ap.add_argument(
        "--dir",
        default=os.environ.get("BENCH_DIR", "/tmp/network-bench"),
        help="Base dir; each scenario uses a subdir (default: /tmp/network-bench)",
    )
    ap.add_argument(
        "--chunk-size",
        type=int,
        default=64,
        help="For hf_fast: chunk size in MB (default 64)",
    )
    args = ap.parse_args()

    scenarios_to_run = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    invalid = [s for s in scenarios_to_run if s not in SCENARIOS]
    if invalid:
        ap.error(f"Invalid scenarios: {invalid}. Valid: {', '.join(SCENARIOS)}")

    base_dir = Path(args.dir)

    print("=" * 60)
    print("Network Download Benchmark (TEE VM)")
    print("=" * 60)
    print(f"Repo:      {args.repo_id} @ {args.revision}")
    print(f"Scenarios: {', '.join(scenarios_to_run)}")
    print()

    # Fetch file list
    print("Fetching HF file tree...")
    tree = fetch_hf_tree(args.repo_id, args.revision)
    largest = get_largest_files(tree, n=args.files)
    if not largest:
        print("No .safetensors files found")
        sys.exit(1)

    total_size = sum(s for _, s in largest)
    print(f"Selected {len(largest)} largest files, total {fmt_size(total_size)}")
    for path, size in largest:
        print(f"  - {path} ({fmt_size(size)})")
    print()

    urls_with_sizes = [
        (build_direct_url(args.repo_id, p, args.revision), s, os.path.basename(p))
        for p, s in largest
    ]
    hf_files = largest  # all scenarios download same N files (apples-to-apples)
    results = {}

    for scenario in scenarios_to_run:
        scenario_dir = base_dir / scenario
        scenario_dir.mkdir(parents=True, exist_ok=True)

        if scenario == "raw":
            print("[raw] Direct HTTP via aria2c or wget (no Python HF)")
            use_aria2c = shutil.which("aria2c") is not None
            raw_result = run_raw_download(urls_with_sizes, scenario_dir, use_aria2c=use_aria2c)
            results["raw"] = raw_result
            if "error" in raw_result:
                print(f"  ERROR: {raw_result['error']}")
            else:
                tool = "aria2c" if use_aria2c else "wget"
                print(f"  Tool: {tool} | {raw_result['mbps']:.2f} MB/s | {raw_result['elapsed']:.1f}s")
            shutil.rmtree(scenario_dir, ignore_errors=True)

        elif scenario == "hf":
            print("[hf] huggingface_hub (httpx, XET disabled)")
            dest = scenario_dir / "files"
            cache = scenario_dir / "cache"
            hf_result = run_hf_download(
                args.repo_id, args.revision, cache, dest,
                files_only=hf_files,
            )
            results["hf"] = hf_result
            if "error" in hf_result:
                print(f"  ERROR: {hf_result['error']}")
            else:
                print(f"  {hf_result['mbps']:.2f} MB/s | {hf_result['elapsed']:.1f}s")

        elif scenario == "hf_fast":
            print(f"[hf_fast] huggingface_hub + patch (no progress, {args.chunk_size}MB chunks)")
            _apply_hf_performance_patch(chunk_size_mb=args.chunk_size)
            dest = scenario_dir / "files"
            cache = scenario_dir / "cache"
            fast_result = run_hf_download(
                args.repo_id, args.revision, cache, dest,
                files_only=hf_files,
            )
            results["hf_fast"] = fast_result
            if "error" in fast_result:
                print(f"  ERROR: {fast_result['error']}")
            else:
                print(f"  {fast_result['mbps']:.2f} MB/s | {fast_result['elapsed']:.1f}s")

        print()

    # Summary
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    for scenario in scenarios_to_run:
        if scenario in results and "error" not in results[scenario]:
            print(f"  {scenario}: {results[scenario]['mbps']:.2f} MB/s ({results[scenario]['elapsed']:.1f}s)")

    if "raw" in results and "error" not in results.get("raw", {}):
        raw_mbps = results["raw"]["mbps"]
        for other in ("hf", "hf_fast"):
            if other in results and "error" not in results.get(other, {}):
                ratio = results[other]["mbps"] / raw_mbps if raw_mbps > 0 else 0
                print(f"\n  {other}/raw: {ratio:.2f}x")
                if other == "hf":
                    if ratio < 0.5:
                        print("  → HF much slower than raw; likely TDX + Python/HF overhead")
                    elif ratio > 0.8:
                        print("  → Similar speeds; network is the bottleneck")
                elif other == "hf_fast" and ratio > 0.5:
                    print("  → Patch helped; GIL/buffer was a bottleneck")
    print()


if __name__ == "__main__":
    main()
