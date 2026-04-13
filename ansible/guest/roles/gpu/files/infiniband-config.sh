#!/usr/bin/env bash
# When no Mellanox/ConnectX PCI devices are present: mask InfiniBand services,
# unload ib_umad, and remove ib_umad from /etc/modules.
# With IB devices present, services run normally. Same VM image for all topologies.

set -euo pipefail

LOG_TAG="infiniband-config"

log() {
    local msg="$1"
    echo "[${LOG_TAG}] ${msg}"
    logger -t "${LOG_TAG}" "${msg}" >/dev/null 2>&1 || true
}

# Detect Mellanox/ConnectX devices via lspci (vendor 15b3)
# Returns device list (one per line) or empty string
get_infiniband_devices() {
    lspci -d 15b3: 2>/dev/null || true
}

devices="$(get_infiniband_devices)"
if [ -n "$devices" ]; then
    count=$(echo "$devices" | wc -l)
    log "InfiniBand devices detected (lspci vendor 15b3): ${count} device(s); skipping mask"
    while IFS= read -r line; do
        [ -n "$line" ] && log "  - $line"
    done <<< "$devices"
    exit 0
fi

log "No InfiniBand PCI devices detected (lspci vendor 15b3); masking services and disabling ib_umad"

# Services that may exist when infiniband-diags/rdma-core/nvlsm are installed.
# Without IB hardware they fail or hang at boot. Stop and mask each.
masked=()
for svc in openibd opensmd rdma-core rdma ibacm rdma-ndd iwpmd nvlsm; do
    if systemctl list-unit-files "${svc}.service" 2>/dev/null | grep -q "${svc}.service"; then
        log "Masking ${svc}.service"
        systemctl stop "${svc}" 2>/dev/null || true
        systemctl mask --runtime "${svc}" 2>/dev/null || true
        masked+=("${svc}.service")
    fi
done

if [ ${#masked[@]} -gt 0 ]; then
    log "Masked ${#masked[@]} service(s): ${masked[*]}"
else
    log "No InfiniBand services present to mask"
fi

# ib_umad: loading without IB hardware can cause ibacm and other services to fail
# (they expect /sys/class/infiniband_mad/abi_version). Unload and remove from /etc/modules.
ib_umad_actions=()
if grep -q '^ib_umad$' /etc/modules 2>/dev/null; then
    log "Removing ib_umad from /etc/modules"
    sed -i '/^ib_umad$/d' /etc/modules
    ib_umad_actions+=("removed from /etc/modules")
fi
if lsmod 2>/dev/null | grep -q '^ib_umad '; then
    log "Unloading ib_umad kernel module"
    modprobe -r ib_umad 2>/dev/null || true
    ib_umad_actions+=("unloaded")
fi

if [ ${#ib_umad_actions[@]} -gt 0 ]; then
    log "ib_umad: ${ib_umad_actions[*]}"
fi

log "infiniband-config completed successfully"
exit 0
