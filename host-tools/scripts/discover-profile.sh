#!/usr/bin/env bash
# discover-profile.sh — Probe host hardware and report GPU metrics.
#
# Collects raw hardware facts (GPU device IDs, CPU topology, NUMA layout,
# InfiniBand, NVSwitches) and writes them to a JSON file. No profile matching
# or verification is performed — use the Python module for that.
#
# Usage:
#   ./discover-profile.sh              # Report to terminal + JSON in CWD
#   ./discover-profile.sh --json-only  # Skip terminal report
#   ./discover-profile.sh --no-json    # Skip JSON output
#
# No root required. nvidia-smi must be accessible (driver loaded).
set -euo pipefail

JSON_OUTPUT=1
REPORT_OUTPUT=1

for arg in "$@"; do
    case "$arg" in
        --json-only) REPORT_OUTPUT=0 ;;
        --no-json)   JSON_OUTPUT=0 ;;
        --help|-h)
            echo "Usage: $0 [--json-only | --no-json]"
            exit 0
            ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

have() { command -v "$1" &>/dev/null; }

bar() {
    if [[ $REPORT_OUTPUT -eq 1 ]]; then
        printf '%s\n' "$(printf '─%.0s' {1..72})"
    fi
}

section() {
    if [[ $REPORT_OUTPUT -eq 1 ]]; then
        echo ""
        printf '\033[1;36m%s\033[0m\n' "$1"
        bar
    fi
}

row() {
    if [[ $REPORT_OUTPUT -eq 1 ]]; then
        printf '  %-32s %s\n' "$1" "$2"
    fi
}

warn() {
    if [[ $REPORT_OUTPUT -eq 1 ]]; then
        printf '  \033[33m⚠  %s\033[0m\n' "$1"
    fi
}

# Convert "Xg" / "XG" / "XM" lspci size string to MiB integer
size_to_mib() {
    local s="${1^^}"   # uppercase
    if [[ "$s" =~ ^([0-9]+)G$ ]]; then
        echo $(( ${BASH_REMATCH[1]} * 1024 ))
    elif [[ "$s" =~ ^([0-9]+)M$ ]]; then
        echo "${BASH_REMATCH[1]}"
    elif [[ "$s" =~ ^([0-9]+)K$ ]]; then
        echo $(( ${BASH_REMATCH[1]} / 1024 ))
    else
        echo "-1"
    fi
}

# Read sysfs NUMA node for a PCI BDF (returns -1 if unknown)
pci_numa_node() {
    local bdf="$1"
    local p="/sys/bus/pci/devices/${bdf}/numa_node"
    if [[ -r "$p" ]]; then
        cat "$p"
    else
        echo "-1"
    fi
}

# Read sysfs cpulist for a NUMA node
numa_cpulist() {
    local node="$1"
    local p="/sys/devices/system/node/node${node}/cpulist"
    [[ -r "$p" ]] && cat "$p" || echo "?"
}

# ---------------------------------------------------------------------------
# CPU topology
# ---------------------------------------------------------------------------
CPU_TOTAL=$(lscpu | awk '/^CPU\(s\):/ {print $2}')
CPU_SOCKETS=$(lscpu | awk '/^Socket\(s\):/ {print $2}')
CPU_CORES_PER_SOCKET=$(lscpu | awk '/^Core\(s\) per socket:/ {print $NF}')
CPU_THREADS_PER_CORE=$(lscpu | awk '/^Thread\(s\) per core:/ {print $NF}')
VCPUS=$(( CPU_TOTAL - 4 ))   # HOST_RESERVED_CPUS = 4

# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
MEM_TOTAL_KB=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
MEM_TOTAL_GB=$(( MEM_TOTAL_KB / 1024 / 1024 ))

# ---------------------------------------------------------------------------
# NUMA nodes
# ---------------------------------------------------------------------------
NUMA_NODES=()
if [[ -d /sys/devices/system/node ]]; then
    for n in /sys/devices/system/node/node[0-9]*; do
        [[ -d "$n" ]] && NUMA_NODES+=("${n##*/node}")
    done
fi
NUMA_NODE_COUNT=${#NUMA_NODES[@]}

declare -A NUMA_CPULIST
for n in "${NUMA_NODES[@]}"; do
    NUMA_CPULIST[$n]=$(numa_cpulist "$n")
done

# ---------------------------------------------------------------------------
# NVIDIA GPUs
# ---------------------------------------------------------------------------
GPU_BDFS=()
GPU_DEVICE_IDS=()
GPU_NUMA_NODES=()

while IFS= read -r line; do
    # e.g. "0000:a1:00.0 3D controller [0302]: NVIDIA ... [10de:2901]"
    bdf=$(echo "$line" | awk '{print $1}')
    dev_id=$(echo "$line" | grep -oP '10de:\K[0-9a-fA-F]{4}')
    numa_n=$(pci_numa_node "$bdf")
    GPU_BDFS+=("$bdf")
    GPU_DEVICE_IDS+=("$dev_id")
    GPU_NUMA_NODES+=("$numa_n")
done < <(lspci -Dnn 2>/dev/null | grep '10de' | grep -E '\[030[02]\]' || true)

GPU_COUNT=${#GPU_BDFS[@]}

# Unique device IDs
declare -A UNIQ_IDS
if [[ ${#GPU_DEVICE_IDS[@]} -gt 0 ]]; then
    for id in "${GPU_DEVICE_IDS[@]}"; do
        UNIQ_IDS[$id]=1
    done
fi
UNIQUE_DEVICE_IDS=("${!UNIQ_IDS[@]}")

# BAR size from first GPU
BAR_SIZE_MB=""
if [[ $GPU_COUNT -gt 0 ]]; then
    bar_line=$(lspci -vvv -s "${GPU_BDFS[0]}" 2>/dev/null | grep -m1 'Region 2' || true)
    if [[ -n "$bar_line" ]]; then
        bar_size_str=$(echo "$bar_line" | grep -oP 'size=\K\S+' | head -1 || true)
        [[ -n "$bar_size_str" ]] && BAR_SIZE_MB=$(size_to_mib "$bar_size_str")
    fi
fi

# VRAM per GPU via nvidia-smi
VRAM_MIB=""
VRAM_GB=""
if have nvidia-smi; then
    vram_line=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ' || true)
    if [[ "$vram_line" =~ ^([0-9]+)MiB$ ]]; then
        VRAM_MIB="${BASH_REMATCH[1]}"
        VRAM_GB=$(( (VRAM_MIB + 512) / 1024 ))
    fi
else
    warn "nvidia-smi not found — VRAM not probed (driver may not be loaded)"
fi

# Suggested ram_per_gpu_gb: VRAM or memory-based fallback
if [[ -n "$VRAM_GB" && $GPU_COUNT -gt 0 ]]; then
    SUGGESTED_RAM_PER_GPU=$VRAM_GB
elif [[ $GPU_COUNT -gt 0 ]]; then
    fallback=$(( (MEM_TOTAL_GB - 64) / GPU_COUNT ))
    SUGGESTED_RAM_PER_GPU=$(( fallback > 0 ? fallback : MEM_TOTAL_GB / GPU_COUNT ))
else
    SUGGESTED_RAM_PER_GPU=0
fi

# ---------------------------------------------------------------------------
# Mellanox / ConnectX NIC detection
# ---------------------------------------------------------------------------
IB_CLASS_DEVICES=()
ETH_CLASS_DEVICES=()
BRIDGE_PFS=()
PASSTHROUGH_CANDIDATES=()

while IFS= read -r line; do
    bdf=$(echo "$line" | awk '{print $1}')
    full_class=$(echo "$line" | grep -oP '\[02[0-9a-fA-F]{2}\]' | tr -d '[]' || true)
    if [[ "$full_class" == "0207" ]]; then
        IB_CLASS_DEVICES+=("$bdf")
    elif [[ "$full_class" == "0200" ]]; then
        ETH_CLASS_DEVICES+=("$bdf")
    fi
done < <(lspci -Dnn | grep '15b3' || true)

# Identify bridge PFs (SMDL=SW_MNG in VPD)
if [[ ${#IB_CLASS_DEVICES[@]} -gt 0 ]]; then
    for bdf in "${IB_CLASS_DEVICES[@]}"; do
        if lspci -vv -s "$bdf" 2>/dev/null | grep -q 'SMDL=SW_MNG' 2>/dev/null; then
            BRIDGE_PFS+=("$bdf")
        else
            PASSTHROUGH_CANDIDATES+=("$bdf")
        fi
    done
fi

# ---------------------------------------------------------------------------
# NVSwitch detection
# ---------------------------------------------------------------------------
NVSWITCH_DEVICES=()
while IFS= read -r line; do
    bdf=$(echo "$line" | awk '{print $1}')
    NVSWITCH_DEVICES+=("$bdf")
done < <(lspci -Dnn | grep '\[0680\]' | grep '10de' || true)

# ---------------------------------------------------------------------------
# Terminal report
# ---------------------------------------------------------------------------
if [[ $REPORT_OUTPUT -eq 1 ]]; then
    HOSTNAME_STR=$(hostname)
    echo ""
    printf '\033[1;37m  GPU Hardware Discovery Report — %s\033[0m\n' "$HOSTNAME_STR"
    bar

    section "GPUs"
    row "Count"             "$GPU_COUNT"
    row "Unique device IDs" "${UNIQUE_DEVICE_IDS[*]:-none}"
    echo ""
    for i in "${!GPU_BDFS[@]}"; do
        row "  GPU $i: ${GPU_BDFS[$i]}" "device=${GPU_DEVICE_IDS[$i]:-?}  numa=${GPU_NUMA_NODES[$i]}"
    done
    echo ""
    row "BAR size (Region 2)"       "${BAR_SIZE_MB:-(not detected)} MB"
    row "VRAM per GPU (nvidia-smi)" "${VRAM_GB:-(not detected)} GB"
    row "Suggested ram_per_gpu_gb"  "${SUGGESTED_RAM_PER_GPU} GB  (${GPU_COUNT}× = $(( GPU_COUNT * SUGGESTED_RAM_PER_GPU )) GB total)"

    section "CPU"
    row "Total CPUs"        "$CPU_TOTAL"
    row "Sockets"           "$CPU_SOCKETS"
    row "Cores per socket"  "$CPU_CORES_PER_SOCKET"
    row "Threads per core"  "$CPU_THREADS_PER_CORE"
    row "vCPUs (cpus - 4)"  "$VCPUS"
    row "SMP topology"      "${VCPUS},sockets=${CPU_SOCKETS},cores=$(( VCPUS / CPU_SOCKETS )),threads=1"

    section "Memory"
    row "Total host RAM"           "${MEM_TOTAL_GB} GB"
    row "Suggested total VM RAM"   "$(( GPU_COUNT * SUGGESTED_RAM_PER_GPU )) GB"

    section "NUMA"
    row "NUMA node count" "$NUMA_NODE_COUNT"
    for n in "${NUMA_NODES[@]:-}"; do
        row "  Node ${n} CPUs" "${NUMA_CPULIST[$n]}"
    done
    echo ""
    echo "  GPU → NUMA node mapping:"
    for i in "${!GPU_BDFS[@]}"; do
        row "    ${GPU_BDFS[$i]}" "node ${GPU_NUMA_NODES[$i]}"
    done

    section "Mellanox / InfiniBand NICs"
    row "IB-class [0207] devices"   "${#IB_CLASS_DEVICES[@]}"
    row "Ethernet-class [0200]"     "${#ETH_CLASS_DEVICES[@]}"
    row "Bridge PFs (SMDL=SW_MNG)"  "${BRIDGE_PFS[*]:-none}"
    row "IB passthrough candidates" "${PASSTHROUGH_CANDIDATES[*]:-none}"

    section "NVSwitches"
    row "NVSwitch devices" "${#NVSWITCH_DEVICES[@]}  (${NVSWITCH_DEVICES[*]:-none})"

    echo ""
fi

# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------
if [[ $JSON_OUTPUT -eq 1 ]]; then
    OUT_FILE="discover-profile-$(hostname)-$(date +%Y%m%dT%H%M%S).json"

    json_str_array() {
        local _var="$1"; shift
        local _arr=("$@")
        local _out="["
        local _sep=""
        for _e in "${_arr[@]}"; do
            _out+="${_sep}\"$(printf '%s' "$_e" | sed 's/\\/\\\\/g; s/"/\\"/g')\""
            _sep=", "
        done
        _out+="]"
        printf -v "$_var" '%s' "$_out"
    }

    json_int_array() {
        local _var="$1"; shift
        local _arr=("$@")
        local _out="["
        local _sep=""
        for _e in "${_arr[@]}"; do
            _out+="${_sep}${_e}"
            _sep=", "
        done
        _out+="]"
        printf -v "$_var" '%s' "$_out"
    }

    if [[ ${#GPU_BDFS[@]} -gt 0 ]]; then
        json_str_array gpu_bdfs_json "${GPU_BDFS[@]}"
        json_str_array gpu_ids_json  "${GPU_DEVICE_IDS[@]}"
        json_int_array gpu_numa_json "${GPU_NUMA_NODES[@]}"
    else
        gpu_bdfs_json="[]"; gpu_ids_json="[]"; gpu_numa_json="[]"
    fi

    if [[ ${#UNIQUE_DEVICE_IDS[@]} -gt 0 ]]; then
        json_str_array uniq_ids_json "${UNIQUE_DEVICE_IDS[@]}"
    else
        uniq_ids_json="[]"
    fi

    numa_nodes_json="["
    numa_cpus_json="{"
    _sep=""
    for n in "${NUMA_NODES[@]:-}"; do
        numa_nodes_json+="${_sep}${n}"
        numa_cpus_json+="${_sep}\"${n}\": \"${NUMA_CPULIST[$n]}\""
        _sep=", "
    done
    numa_nodes_json+="]"
    numa_cpus_json+="}"

    if [[ ${#IB_CLASS_DEVICES[@]} -gt 0 ]]; then
        json_str_array ib_json "${IB_CLASS_DEVICES[@]}"
    else
        ib_json="[]"
    fi

    if [[ ${#BRIDGE_PFS[@]} -gt 0 ]]; then
        json_str_array bridge_json "${BRIDGE_PFS[@]}"
    else
        bridge_json="[]"
    fi

    if [[ ${#PASSTHROUGH_CANDIDATES[@]} -gt 0 ]]; then
        json_str_array passthru_json "${PASSTHROUGH_CANDIDATES[@]}"
    else
        passthru_json="[]"
    fi

    if [[ ${#NVSWITCH_DEVICES[@]} -gt 0 ]]; then
        json_str_array nvswitch_json "${NVSWITCH_DEVICES[@]}"
    else
        nvswitch_json="[]"
    fi

    cat > "$OUT_FILE" <<JSON
{
  "hostname": "$(hostname)",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "gpu": {
    "pci_device_ids": ${uniq_ids_json},
    "bdfs": ${gpu_bdfs_json},
    "count": ${GPU_COUNT},
    "vram_gb": ${VRAM_GB:-null},
    "bar_size_mb": ${BAR_SIZE_MB:--1},
    "numa_nodes": ${gpu_numa_json}
  },
  "cpu": {
    "total": ${CPU_TOTAL},
    "sockets": ${CPU_SOCKETS},
    "cores_per_socket": ${CPU_CORES_PER_SOCKET},
    "threads_per_core": ${CPU_THREADS_PER_CORE},
    "vcpus": ${VCPUS}
  },
  "memory": {
    "total_gb": ${MEM_TOTAL_GB},
    "suggested_ram_per_gpu_gb": ${SUGGESTED_RAM_PER_GPU},
    "suggested_total_vm_ram_gb": $(( GPU_COUNT * SUGGESTED_RAM_PER_GPU ))
  },
  "numa": {
    "node_count": ${NUMA_NODE_COUNT},
    "nodes": ${numa_nodes_json},
    "cpus_per_node": ${numa_cpus_json}
  },
  "nic": {
    "ib_class_count": ${#IB_CLASS_DEVICES[@]},
    "eth_class_count": ${#ETH_CLASS_DEVICES[@]},
    "ib_devices": ${ib_json},
    "bridge_pfs": ${bridge_json},
    "passthrough_candidates": ${passthru_json}
  },
  "nvswitch": {
    "present": $( [[ ${#NVSWITCH_DEVICES[@]} -gt 0 ]] && echo 'true' || echo 'false' ),
    "count": ${#NVSWITCH_DEVICES[@]},
    "devices": ${nvswitch_json}
  }
}
JSON

    if [[ $REPORT_OUTPUT -eq 1 ]]; then
        printf '  JSON written to: %s\n\n' "$OUT_FILE"
    else
        echo "$OUT_FILE"
    fi
fi
