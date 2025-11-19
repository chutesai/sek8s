#!/usr/bin/env bash
# unbind-all-nvidia.sh — unbind every NVIDIA GPU/NVSwitch from vfio-pci
# and optionally rebind to the NVIDIA driver if present.
#
# Run as root:  sudo ./unbind-all-nvidia.sh

set -euo pipefail

need_root() { [[ $(id -u) -eq 0 ]] || { echo "Run as root." >&2; exit 1; }; }
have_drvctl() { command -v driverctl &>/dev/null; }

unbind_dev() {                 # $1 = 0000:18:00.0
    local dev="$1"

    local cur
    cur=$(basename "$(readlink "/sys/bus/pci/devices/$dev/driver" 2>/dev/null)" 2>/dev/null || echo none)

    if [[ $cur == vfio-pci ]]; then
        echo "  • unbinding $dev from vfio-pci"
        echo "$dev" > /sys/bus/pci/drivers/vfio-pci/unbind
    else
        echo "  • $dev not on vfio-pci (current=$cur)"
    fi

    # Clear override if present
    if [[ -f /sys/bus/pci/devices/$dev/driver_override ]]; then
        echo "" > /sys/bus/pci/devices/$dev/driver_override
    fi

    # Attempt to bind back to nvidia if driver loaded
    if [[ -d /sys/bus/pci/drivers/nvidia ]]; then
        echo "  • rebinding $dev → nvidia"
        echo "$dev" > /sys/bus/pci/drivers/nvidia/bind || true
    else
        echo "  • nvidia driver not loaded — leaving $dev driverless"
    fi
}

need_root

# detect devices
mapfile -t gpus < <(
    lspci -Dn | awk '$2 ~ /^(0300|0302):/ && $3 ~ /^10de:/ {print $1}'
)
mapfile -t nvsw < <(
    lspci -Dn | awk '$2 ~ /^0680:/ && $3 ~ /^10de:22a3/ {print $1}'
)

echo "Detected devices:"
echo "  GPUs: ${gpus[*]:-none}"
echo "  NVSwitch: ${nvsw[*]:-none}"
echo

declare -A groups_done

for dev in "${gpus[@]}" "${nvsw[@]}"; do
    [[ -e /sys/bus/pci/devices/$dev/iommu_group ]] || continue
    grp=$(basename "$(readlink -f /sys/bus/pci/devices/$dev/iommu_group)")
    if [[ -n ${groups_done[$grp]:-} ]]; then continue; fi
    groups_done["$grp"]=1

    echo "▶ Group $grp"
    for node in "/sys/bus/pci/devices/$dev/iommu_group/devices/"*; do
        fn=$(basename "$node")
        unbind_dev "$fn"
    done
    echo
done

echo "✔ All NVIDIA IOMMU groups unbound from vfio-pci."
echo "Verify with:"
echo "  lspci -k | grep -A3 -E '(NVIDIA|NVSwitch)'"
