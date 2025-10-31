#!/usr/bin/env bash
# bind-all-nvidia-complete.sh — bind every NVIDIA GPU and NVSwitch IOMMU-group to vfio-pci
#
# Run as root:  sudo ./bind-all-nvidia-complete.sh
# Check result: ls -l /dev/vfio ; lspci -k | grep -A3 NVIDIA

set -euo pipefail

need_root()   { [[ $(id -u) -eq 0 ]] || { echo "Run as root." >&2; exit 1; }; }
load_vfio()   { for m in vfio_pci vfio_iommu_type1 vfio_virqfd; do modprobe "$m" 2>/dev/null || true; done; }
have_drvctl() { command -v driverctl &>/dev/null; }

bind_vfio() {                      # $1 = 0000:18:00.0
    local dev="$1"
    if have_drvctl; then
        driverctl -v set-override "$dev" vfio-pci
    else
        echo vfio-pci > /sys/bus/pci/devices/$dev/driver_override
        echo "$dev"   > /sys/bus/pci/drivers_probe
    fi
}

bind_group() {                                # $1 = 0000:18:00.0
    local dev="$1" grp dir
    dir=$(readlink -f "/sys/bus/pci/devices/$dev/iommu_group") || {
        echo "✗ $dev: no IOMMU group (enable IOMMU in BIOS/kernel)" >&2; return; }
    grp=$(basename "$dir")
    echo "▶ Binding group $grp (triggered by $dev)"
    for node in "$dir"/devices/*; do
        fn=$(basename "$node")
        cur=$(basename "$(readlink "$node/driver" 2>/dev/null)" 2>/dev/null || echo none)
    
        if [[ $cur == vfio-pci ]]; then
            echo "  • $fn already on vfio-pci"
            continue
        fi
    
        # Check if this is a bridge device that can't be bound to vfio-pci
        class=$(cat "/sys/bus/pci/devices/$fn/class" 2>/dev/null || echo "")
        if [[ $class == "0x060400" ]]; then
            echo "  • $fn is a PCIe bridge (skipping)"
            continue
        fi
    
        if [[ -n $cur && $cur != none ]]; then
            echo "    unbind $fn from $cur"
            echo "$fn" > "/sys/bus/pci/drivers/$cur/unbind" 2>/dev/null || {
                echo "    warning: couldn't unbind $fn from $cur"
            }
        else
            echo "  • $fn currently driverless"
        fi
    
        bind_vfio "$fn"
        echo "  ✓ $fn → vfio-pci"
    done
    echo
}

###############################################################################
# main
###############################################################################
need_root
load_vfio

# Detect every NVIDIA GPU (class 0300 or 0302)
mapfile -t gpus < <(
    lspci -Dn | awk '$2 ~ /^(0300|0302):/ && $3 ~ /^10de:/ {print $1}'
)

# Detect every NVSwitch (class 0680, vendor 10de, device 22a3)
mapfile -t nvswitches < <(
    lspci -Dn | awk '$2 ~ /^0680:/ && $3 ~ /^10de:22a3/ {print $1}'
)

echo "Found NVIDIA devices:"
echo "  GPUs: ${gpus[*]:-none}"
echo "  NVSwitch: ${nvswitches[*]:-none}"
echo

# Collect all unique IOMMU groups
declare -A groups_done

# Process GPUs
for dev in "${gpus[@]}"; do
    if [[ -e "/sys/bus/pci/devices/$dev/iommu_group" ]]; then
        grp=$(basename "$(readlink -f "/sys/bus/pci/devices/$dev/iommu_group")")
        if [[ -z "${groups_done[$grp]:-}" ]]; then
            bind_group "$dev"
            groups_done[$grp]=1
        fi
    fi
done

# Process NVSwitch devices
for dev in "${nvswitches[@]}"; do
    if [[ -e "/sys/bus/pci/devices/$dev/iommu_group" ]]; then
        grp=$(basename "$(readlink -f "/sys/bus/pci/devices/$dev/iommu_group")")
        if [[ -z "${groups_done[$grp]:-}" ]]; then
            bind_group "$dev"
            groups_done[$grp]=1
        fi
    fi
done

echo "✓ All NVIDIA device IOMMU groups processed."
echo "  Verify with: ls -l /dev/vfio ; lspci -k | grep -A3 -E '(NVIDIA|NVSwitch)'"

# Show final status
echo
echo "=== Final binding status ==="
echo "GPUs:"
for dev in "${gpus[@]}"; do
    printf "  %-12s " "$dev:"
    if [[ -e "/sys/bus/pci/devices/$dev/driver" ]]; then
        basename "$(readlink "/sys/bus/pci/devices/$dev/driver")"
    else
        echo "no driver"
    fi
done

echo
echo "NVSwitch devices:"
for dev in "${nvswitches[@]}"; do
    printf "  %-12s " "$dev:"
    if [[ -e "/sys/bus/pci/devices/$dev/driver" ]]; then
        basename "$(readlink "/sys/bus/pci/devices/$dev/driver")"
    else
        echo "no driver"
    fi
done
