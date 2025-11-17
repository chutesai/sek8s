#! /bin/bash
#!/bin/bash
# show-passthrough-devices.sh

echo "=== All NVIDIA Devices ==="
lspci -nn -d 10de: -D | while read -r line; do
  pci_addr=$(echo "$line" | awk '{print $1}')
  
  # Get driver if bound
  if [ -L "/sys/bus/pci/devices/$pci_addr/driver" ]; then
    driver=$(readlink /sys/bus/pci/devices/$pci_addr/driver | xargs basename)
  else
    driver="(none)"
  fi
  
  # Check reset capability
  if [ -e "/sys/bus/pci/devices/$pci_addr/reset" ]; then
    reset_cap="[reset: yes]"
  else
    reset_cap="[reset: no]"
  fi
  
  echo "$line"
  echo "  └─ Driver: $driver $reset_cap"
done

echo -e "\n=== VFIO-PCI Bound Devices ==="
if [ -d "/sys/bus/pci/drivers/vfio-pci" ]; then
  ls -1 /sys/bus/pci/drivers/vfio-pci/ | grep "^0" | while read -r pci_addr; do
    desc=$(lspci -s ${pci_addr#0000:} -nn)
    echo "$pci_addr: $desc"
  done
else
  echo "No devices bound to vfio-pci"
fi
