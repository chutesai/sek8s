#!/bin/bash
# reset-vfio-devices.sh - Reset all VFIO-PCI bound devices
# Useful for clearing GPU state before launching TDX VMs

set -e

reset_vfio_devices() {
  echo "Resetting all VFIO-PCI bound devices..."
  
  # Check if vfio-pci driver is loaded
  if [ ! -d "/sys/bus/pci/drivers/vfio-pci" ]; then
    echo "Error: vfio-pci driver not loaded"
    echo "Run your bind script first to bind devices to vfio-pci"
    exit 1
  fi
  
  # Count devices
  DEVICE_COUNT=$(ls -1 /sys/bus/pci/drivers/vfio-pci/ | grep -c "^0" || echo "0")
  
  if [ "$DEVICE_COUNT" -eq 0 ]; then
    echo "No devices bound to vfio-pci"
    exit 0
  fi
  
  echo "Found $DEVICE_COUNT device(s) bound to vfio-pci"
  echo ""
  
  # Reset each device
  RESET_COUNT=0
  UNBIND_COUNT=0
  SKIP_COUNT=0
  
  for device in /sys/bus/pci/drivers/vfio-pci/0*; do
    [ -e "$device" ] || continue
    
    pci_addr=$(basename "$device")
    
    # Get device description
    desc=$(lspci -s ${pci_addr#0000:} -nn 2>/dev/null || echo "Unknown device")
    
    # Try FLR (Function Level Reset) first
    if [ -e "$device/reset" ]; then
      echo "Resetting $pci_addr (FLR)"
      echo "  └─ $desc"
      
      if echo 1 > "$device/reset" 2>/dev/null; then
        RESET_COUNT=$((RESET_COUNT + 1))
        sleep 0.5
      else
        # FLR failed, try unbind/rebind
        echo "  └─ FLR failed, trying unbind/rebind"
        echo $pci_addr > /sys/bus/pci/drivers/vfio-pci/unbind 2>/dev/null || {
          echo "  └─ Failed to unbind $pci_addr"
          SKIP_COUNT=$((SKIP_COUNT + 1))
          continue
        }
        sleep 0.5
        echo $pci_addr > /sys/bus/pci/drivers/vfio-pci/bind 2>/dev/null || {
          echo "  └─ Failed to rebind $pci_addr"
          SKIP_COUNT=$((SKIP_COUNT + 1))
          continue
        }
        UNBIND_COUNT=$((UNBIND_COUNT + 1))
        sleep 0.5
      fi
    else
      echo "Skipping $pci_addr (no reset capability)"
      echo "  └─ $desc"
      SKIP_COUNT=$((SKIP_COUNT + 1))
    fi
  done
  
  echo ""
  echo "=== Reset Summary ==="
  echo "FLR resets:      $RESET_COUNT"
  echo "Unbind/rebind:   $UNBIND_COUNT"
  echo "Skipped:         $SKIP_COUNT"
  echo "Total devices:   $DEVICE_COUNT"
  echo ""
  echo "✓ VFIO device reset complete"
}

# Parse arguments
case "${1:-}" in
  --help|-h)
    cat << EOF
Usage: $0 [options]

Reset all devices bound to vfio-pci driver.

Options:
  --help, -h     Show this help message
  --dry-run      Show what would be reset without actually resetting

This script attempts to reset devices using:
1. Function Level Reset (FLR) if available
2. Unbind/rebind from vfio-pci as fallback

Useful for clearing GPU state before launching VMs to avoid
intermittent initialization failures.
EOF
    exit 0
    ;;
  --dry-run)
    echo "=== Dry Run - Devices that would be reset ==="
    if [ -d "/sys/bus/pci/drivers/vfio-pci" ]; then
      for device in /sys/bus/pci/drivers/vfio-pci/0*; do
        [ -e "$device" ] || continue
        pci_addr=$(basename "$device")
        desc=$(lspci -s ${pci_addr#0000:} -nn 2>/dev/null || echo "Unknown device")
        
        if [ -e "$device/reset" ]; then
          echo "$pci_addr [FLR supported]"
        else
          echo "$pci_addr [unbind/rebind only]"
        fi
        echo "  └─ $desc"
      done
    else
      echo "No vfio-pci devices found"
    fi
    exit 0
    ;;
  "")
    # No arguments, proceed with reset
    reset_vfio_devices
    ;;
  *)
    echo "Unknown option: $1"
    echo "Use --help for usage information"
    exit 1
    ;;
esac

exit 0
