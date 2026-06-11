#!/usr/bin/env bash
# discover-profile.sh — Probe hardware and generate GPU profile metrics.
#
# Collects all values needed to verify or author a GpuProfile entry in
# host-tools/scripts/chutes/guest/gpu/profiles.py.
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
# Known profile matching
# ---------------------------------------------------------------------------
# Map device_id -> profile name, expected bar_size_mb, expected host_cpus
declare -A PROFILE_NAME=( [2901]="B200" [3182]="B300" [2335]="H200" [2bb1]="RTX_PRO_6000" [2bb5]="RTX_PRO_6000" )
declare -A PROFILE_BAR=(  [2901]=262144  [3182]=524288  [2335]=262144 [2bb1]=131072       [2bb5]=131072 )
declare -A PROFILE_CPUS=( [2901]=192     [3182]=192     [2335]=128    [2bb1]=128           [2bb5]=128 )

MATCHED_PROFILE=""
if [[ ${#UNIQUE_DEVICE_IDS[@]} -gt 0 ]]; then
    for id in "${UNIQUE_DEVICE_IDS[@]}"; do
        id_lower="${id,,}"
        if [[ -n "${PROFILE_NAME[$id_lower]:-}" ]]; then
            MATCHED_PROFILE="${PROFILE_NAME[$id_lower]}"
        fi
    done
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
    pci_class=$(echo "$line" | grep -oP '\[02\K[0-9a-fA-F]{2}(?=\])' || true)
    # Full class is [0207] or [0200]
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
# NVSwitches are NVIDIA (10de) PCI class 0680 (Other Bridge).
# lspci -Dnn format: "BDF Class [classcode]: Vendor Desc [vendorid:devid]"
# [0680] appears before the vendor ID so we match class first, then filter 10de.
done < <(lspci -Dnn 2>/dev/null | grep '\[0680\]' | grep '10de' || true)

# ---------------------------------------------------------------------------
# Profile verification notes
# ---------------------------------------------------------------------------
NOTES=()
if [[ -n "$MATCHED_PROFILE" && ${#UNIQUE_DEVICE_IDS[@]} -gt 0 ]]; then
    id_lower="${UNIQUE_DEVICE_IDS[0],,}"
    expected_bar=${PROFILE_BAR[$id_lower]:-0}
    expected_cpus=${PROFILE_CPUS[$id_lower]:-0}

    if [[ -n "$BAR_SIZE_MB" && "$BAR_SIZE_MB" != "-1" && "$BAR_SIZE_MB" != "$expected_bar" ]]; then
        NOTES+=("BAR size mismatch: profile expects ${expected_bar} MB, hardware shows ${BAR_SIZE_MB} MB")
    fi
    if [[ "$CPU_TOTAL" != "$expected_cpus" ]]; then
        NOTES+=("CPU count mismatch: profile expects ${expected_cpus}, hardware has ${CPU_TOTAL}")
    fi
fi

# ---------------------------------------------------------------------------
# Terminal report
# ---------------------------------------------------------------------------
if [[ $REPORT_OUTPUT -eq 1 ]]; then
    HOSTNAME_STR=$(hostname)
    echo ""
    printf '\033[1;37m  GPU Profile Discovery Report — %s\033[0m\n' "$HOSTNAME_STR"
    bar

    section "GPUs"
    row "Count" "$GPU_COUNT"
    row "Unique device IDs" "${UNIQUE_DEVICE_IDS[*]:-none}"
    row "Matched profile" "${MATCHED_PROFILE:-"(unknown — new hardware?)"}"
    echo ""
    for i in "${!GPU_BDFS[@]}"; do
        row "  GPU $i: ${GPU_BDFS[$i]}" "device=${GPU_DEVICE_IDS[$i]:-?}  numa=${GPU_NUMA_NODES[$i]}"
    done
    echo ""
    row "BAR size (Region 2)"       "${BAR_SIZE_MB:-(not detected)} MB  → bar_size_mb"
    row "VRAM per GPU (nvidia-smi)" "${VRAM_GB:-(not detected)} GB  → vram_gb"
    row "Suggested ram_per_gpu_gb"  "${SUGGESTED_RAM_PER_GPU} GB  (= ${GPU_COUNT}×${SUGGESTED_RAM_PER_GPU} = $(( GPU_COUNT * SUGGESTED_RAM_PER_GPU )) GB total VM RAM)"

    section "CPU"
    row "Total CPUs"          "${CPU_TOTAL}  → host_cpus"
    row "Sockets"             "${CPU_SOCKETS}  → host_sockets"
    row "Cores per socket"    "$CPU_CORES_PER_SOCKET"
    row "Threads per core"    "$CPU_THREADS_PER_CORE"
    row "vCPUs (cpus - 4)"   "${VCPUS}"
    row "SMP topology"        "${VCPUS},sockets=${CPU_SOCKETS},cores=$(( VCPUS / CPU_SOCKETS )),threads=1"

    section "Memory"
    row "Total host RAM"          "${MEM_TOTAL_GB} GB"
    row "Total VM RAM (suggested)" "$(( GPU_COUNT * SUGGESTED_RAM_PER_GPU )) GB"

    section "NUMA"
    row "NUMA node count"   "$NUMA_NODE_COUNT"
    for n in "${NUMA_NODES[@]:-}"; do
        row "  Node ${n} CPUs"   "${NUMA_CPULIST[$n]}"
    done
    echo ""
    echo "  GPU → NUMA node mapping:"
    for i in "${!GPU_BDFS[@]}"; do
        row "    ${GPU_BDFS[$i]}" "node ${GPU_NUMA_NODES[$i]}"
    done

    section "Mellanox / InfiniBand NICs"
    row "IB-class [0207] devices"  "${#IB_CLASS_DEVICES[@]}"
    row "Ethernet-class [0200]"    "${#ETH_CLASS_DEVICES[@]}"
    row "Bridge PFs (SMDL=SW_MNG)" "${BRIDGE_PFS[*]:-none}"
    row "IB passthrough candidates" "${PASSTHROUGH_CANDIDATES[*]:-none}"

    section "NVSwitches"
    row "NVSwitch devices" "${#NVSWITCH_DEVICES[@]}  (${NVSWITCH_DEVICES[*]:-none})"
    row "should_passthrough_nvswitches" "$( [[ ${#NVSWITCH_DEVICES[@]} -gt 0 ]] && echo 'True (present)' || echo 'False (none detected)' )"

    section "Profile Property Summary"
    printf '  %-35s %s\n' "Property" "Value"
    bar
    printf '  %-35s %s\n' "pci_device_ids"           "[\"${UNIQUE_DEVICE_IDS[*]:-?}\"]"
    printf '  %-35s %s\n' "host_cpus"                "$CPU_TOTAL"
    printf '  %-35s %s\n' "host_sockets"             "$CPU_SOCKETS"
    printf '  %-35s %s\n' "vram_gb"                  "${VRAM_GB:-(check nvidia-smi)}"
    printf '  %-35s %s\n' "ram_per_gpu_gb"           "$SUGGESTED_RAM_PER_GPU"
    printf '  %-35s %s\n' "bar_size_mb"              "${BAR_SIZE_MB:-(not detected)}"
    printf '  %-35s %s\n' "should_passthrough_infiniband" "$( [[ ${#PASSTHROUGH_CANDIDATES[@]} -gt 0 ]] && echo 'True' || echo 'False' )"
    printf '  %-35s %s\n' "should_passthrough_nvswitches" "$( [[ ${#NVSWITCH_DEVICES[@]} -gt 0 ]] && echo 'True (for 8-GPU)' || echo 'False' )"

    if [[ ${#NOTES[@]} -gt 0 ]]; then
        echo ""
        printf '\033[1;33m  Verification Notes:\033[0m\n'
        for note in "${NOTES[@]}"; do
            warn "$note"
        done
    fi
    echo ""
fi

# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------
if [[ $JSON_OUTPUT -eq 1 ]]; then
    OUT_FILE="discover-profile-$(hostname)-$(date +%Y%m%dT%H%M%S).json"

    # Helper: build a JSON string array from bash array elements.
    # Usage: json_str_array result_var arr[@]
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

    # Helper: build a JSON int array from bash array elements.
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

    # Build arrays
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

    if [[ ${#NOTES[@]} -gt 0 ]]; then
        json_str_array notes_json "${NOTES[@]}"
    else
        notes_json="[]"
    fi

    # matched_profile as a JSON value (null or quoted string)
    if [[ -n "$MATCHED_PROFILE" ]]; then
        matched_profile_json="\"${MATCHED_PROFILE}\""
    else
        matched_profile_json="null"
    fi

    cat > "$OUT_FILE" <<JSON
{
  "hostname": "$(hostname)",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "matched_profile": ${matched_profile_json},
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
    "passthrough_candidates": ${passthru_json},
    "should_passthrough_infiniband": $( [[ ${#PASSTHROUGH_CANDIDATES[@]} -gt 0 ]] && echo 'true' || echo 'false' )
  },
  "nvswitch": {
    "present": $( [[ ${#NVSWITCH_DEVICES[@]} -gt 0 ]] && echo 'true' || echo 'false' ),
    "count": ${#NVSWITCH_DEVICES[@]},
    "devices": ${nvswitch_json}
  },
  "profile_properties": {
    "pci_device_ids": ${uniq_ids_json},
    "host_cpus": ${CPU_TOTAL},
    "host_sockets": ${CPU_SOCKETS},
    "vram_gb": ${VRAM_GB:-null},
    "ram_per_gpu_gb": ${SUGGESTED_RAM_PER_GPU},
    "bar_size_mb": ${BAR_SIZE_MB:--1},
    "should_passthrough_infiniband": $( [[ ${#PASSTHROUGH_CANDIDATES[@]} -gt 0 ]] && echo 'true' || echo 'false' ),
    "should_passthrough_nvswitches": $( [[ ${#NVSWITCH_DEVICES[@]} -gt 0 ]] && echo 'true' || echo 'false' )
  },
  "verification_notes": ${notes_json}
}
JSON

    if [[ $REPORT_OUTPUT -eq 1 ]]; then
        printf '  JSON written to: %s\n\n' "$OUT_FILE"
    else
        echo "$OUT_FILE"
    fi
fi
