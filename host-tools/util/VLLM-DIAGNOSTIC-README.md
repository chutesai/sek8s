# vLLM Load Diagnostic — Usage Guide

This document describes how to run the vLLM load diagnostic script (`vllm-load-diagnostic.py`) for debugging model load performance and GPU memory fragmentation in the Chutes environment.

## Overview

The script reproduces the **same runtime configuration as the Chutes vLLM template** (`build_vllm_chute`), including:

- Environment variables (HF_HOME, NCCL_*, VLLM_*, etc.)
- Tensor parallel configuration
- vLLM initialization parameters
- CUDA graph capture configuration
- Cache directory setup (`set_default_cache_dirs`)
- NCCL flags for H200/H100 (`set_nccl_flags`)

Findings from this diagnostic can be directly translated into production template changes.

---

## Prerequisites

- Python 3.12
- `torch`, `vllm`, `psutil`, `huggingface_hub`
- NVIDIA GPUs (e.g. H200, ~141GB VRAM)
- Model already downloaded or network access for HuggingFace

---

## Running the Diagnostic

### 1. Full diagnostic (recommended first run)

Run all phases sequentially in one pass:

```bash
cd host-tools/util
python vllm-load-diagnostic.py
```

This will:

1. Set Chutes-compatible environment
2. Resolve HF snapshot (download if needed)
3. Load model with tensor parallelism
4. Capture memory snapshots at each stage
5. Run warmup inference
6. Print timeline, disk/PCIe throughput, fragmentation metrics

**When to use:** Initial baseline, full picture of load behavior.

---

### 2. Isolate CUDA graph capture effects

If you suspect CUDA graph capture is causing OOM:

```bash
# Disable CUDA graphs — compare load time and success
python vllm-load-diagnostic.py --disable-cudagraph
```

Compare with a normal run. If `--disable-cudagraph` succeeds but the default fails, CUDA graph capture is likely the culprit.

---

### 3. Customize CUDA graph capture sizes

Some OOMs occur because vLLM captures graphs for batch sizes that require more memory than available:

```bash
python vllm-load-diagnostic.py --cudagraph-sizes 1,2,4,8
```

Use smaller sizes if you see OOM during capture. The default captures many sizes; restricting can reduce peak memory.

---

### 4. Multi-instance simulation (reproduce second-instance OOM)

To reproduce the "second instance OOM during CUDA graph capture" scenario:

```bash
python vllm-load-diagnostic.py --spawn-second-instance --tensor-parallel 4
```

This loads the model once, then spawns a second process loading the same model on GPUs 4–7 (for 8-GPU systems). Compare:

- Memory layout and fragmentation of first vs second instance
- Whether the second instance fails with OOM

---

### 5. Cold vs warm load (disk and page cache)

**Cold load** (data from disk):

```bash
# On the host/VM, drop page cache first
sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'

python vllm-load-diagnostic.py
```

**Warm load** (data from page cache):

```bash
# Run twice — second run uses cache
python vllm-load-diagnostic.py
python vllm-load-diagnostic.py
```

Compare disk read bytes and effective disk throughput. If the second run shows very low disk read and high throughput, the first run was cold.

---

### 6. Specific model and tensor parallel size

```bash
python vllm-load-diagnostic.py \
  --model MiniMaxAI/MiniMax-M2.5 \
  --revision main \
  --tensor-parallel 8
```

Use `--revision` to lock to a specific HuggingFace commit (Chutes requires this for reproducibility).

---

### 7. Custom HF cache location

```bash
export HF_HOME=/var/snap/cache
python vllm-load-diagnostic.py

# Or:
python vllm-load-diagnostic.py --hf-home /var/snap/cache
```

---

## Output Interpretation

### MODEL LOAD TIMELINE

| Phase | Meaning |
|-------|---------|
| Python startup + CUDA init | Import and CUDA setup |
| HF snapshot resolve | HuggingFace download/resolution |
| Model load | Weights + KV cache + CUDA graph capture |
| Warmup inference | First `generate()` call |
| TOTAL | End-to-end time |

### GPU MEMORY SNAPSHOT

- **Total/Used/Free**: From `nvidia-smi`
- **Reserved/Allocated**: PyTorch allocator
- **Fragmentation**: HIGH when `reserved/active` > 1.2 or `inactive_split` is large

If you see **~20GB free but OOM**, check fragmentation. High fragmentation means free memory is in small blocks and cannot satisfy a large allocation (e.g. CUDA graph capture).

### DISK & PCIe DIAGNOSTICS

- **Effective disk throughput**: `disk_read_GB / load_time` — low values suggest disk or page cache bottleneck
- **Effective PCIe throughput**: `VRAM_loaded / load_time` — helps distinguish disk vs PCIe vs CPU bottlenecks

### Warnings

- `HIGH FRAGMENTATION at after_load` — may cause OOM during CUDA graph capture
- Consider: `--disable-cudagraph`, lower `gpu_memory_utilization`, or `--cudagraph-sizes` with smaller values

---

## Sequential vs Iterative Runs

### Sequential (default)

Run everything in one pass. Best for:

- Initial baseline
- Capturing a full timeline
- When load succeeds and you want a complete report

### Iterative (run phases separately)

For deep debugging, run phases one at a time and inspect results:

1. **HF snapshot only** (no model load):

   ```bash
   python -c "
   from huggingface_hub import snapshot_download
   import time
   t0 = time.perf_counter()
   snapshot_download('MiniMaxAI/MiniMax-M2.5')
   print('HF resolve:', time.perf_counter()-t0, 's')
   "
   ```

2. **Model load with diagnostics**:

   ```bash
   python vllm-load-diagnostic.py --disable-cudagraph
   ```

3. **Second instance** (after first load in another terminal):

   ```bash
   CUDA_VISIBLE_DEVICES=4,5,6,7 python vllm-load-diagnostic.py --tensor-parallel 4 --no-print-topology
   ```

Iterate based on where failures or slowdowns occur.

---

## Environment Variables (Chutes Parity)

The script sets these to match Chutes templates:

| Variable | Purpose |
|----------|---------|
| `VLLM_WORKER_MULTIPROC_METHOD` | `spawn` (Chutes requirement) |
| `HF_HOME` | Cache root (e.g. `/var/snap/cache`) |
| `HF_HUB_CACHE` | `$HF_HOME/hub` |
| `TRANSFORMERS_CACHE` | `$HF_HOME/transformers` |
| `HF_HUB_DISABLE_XET` | `1` (sek8s) |
| `HF_HUB_ENABLE_HF_TRANSFER` | `1` (faster downloads) |
| `VLLM_DISABLE_TELEMETRY` | `1` |
| `CUDA_DEVICE_COUNT` | Override GPU count |
| `NCCL_*` | Cleared for H200/H100 (P2P, IB, GDR) |

Cache dirs (`TRITON_CACHE_DIR`, `VLLM_CACHE_ROOT`, etc.) are set under the download path per Chutes `set_default_cache_dirs`.

---

## TDX / Confidential VM Notes

- PPCIe mode: Chutes uses `--disable-custom-all-reduce` for TDX+PPCIe
- The diagnostic uses the same NCCL and vLLM settings
- TDX memory overhead can reduce effective VRAM; monitor fragmentation closely
- For cold load testing, ensure the cache volume has sufficient I/O bandwidth

---

## Troubleshooting

| Issue | Suggestion |
|-------|------------|
| OOM during load | Try `--disable-cudagraph` or reduce `--tensor-parallel` |
| OOM on second instance | Run `--spawn-second-instance` and compare fragmentation |
| Very slow load | Check disk throughput; try cold vs warm; inspect `nvidia-smi` during load |
| Import errors | Ensure `vllm`, `torch`, `psutil`, `huggingface_hub` installed |
| `mem_get_info` missing | PyTorch 2.4+ required for some memory stats |

---

## Reference

- Chutes vLLM template: https://github.com/chutesai/chutes (chutes/chute/template/vllm.py)
- Chutes helpers: chutes/chute/template/helpers.py
- vLLM CUDA graphs: https://docs.vllm.ai/en/latest/design/cuda_graphs.html
