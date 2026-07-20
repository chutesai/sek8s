#!/bin/bash
# extract-measurements.sh - Capture a guest's CCEL + ACPI/SMBIOS measurement preimages

BOOT_NUM=${1:-auto}
BASE_DIR="rtmr_snapshots"

# Auto-increment boot number if not specified
if [ "$BOOT_NUM" == "auto" ]; then
    BOOT_NUM=1
    while [ -d "${BASE_DIR}_boot${BOOT_NUM}" ]; do
        BOOT_NUM=$((BOOT_NUM + 1))
    done
fi

OUTPUT_DIR="${BASE_DIR}_boot${BOOT_NUM}"
mkdir -p "$OUTPUT_DIR"

echo "==================================="
echo "Capturing RTMR snapshot: Boot $BOOT_NUM"
echo "Output directory: $OUTPUT_DIR"
echo "==================================="

# Generate TDX quote (adjust path to your quote generator)
cd /home/tdx
echo "Generating TDX quote..."
tdx-quote-generator -o "$OUTPUT_DIR/quote.bin" 2>&1
QUOTE_EXIT=$?

# Capture system state for reference
echo "Capturing system state..."
cat /proc/cmdline > "$OUTPUT_DIR/cmdline.txt"
uptime > "$OUTPUT_DIR/uptime.txt"
dmesg | head -100 > "$OUTPUT_DIR/dmesg.txt"
date > "$OUTPUT_DIR/timestamp.txt"

# Capture UEFI variables
echo "Capturing UEFI variables..."
ls -la /sys/firmware/efi/efivars/ > "$OUTPUT_DIR/efivars_list.txt"

# Capture specific UEFI variables that might change
VARS_TO_CHECK=("BootCurrent" "BootOrder" "MTC" "NvVars" "VarErrorFlag")
for var in "${VARS_TO_CHECK[@]}"; do
    VAR_FILE=$(find /sys/firmware/efi/efivars/ -name "$var-*" 2>/dev/null | head -1)
    if [ -n "$VAR_FILE" ]; then
        xxd "$VAR_FILE" > "$OUTPUT_DIR/efivar_${var}.txt" 2>/dev/null || true
    fi
done

# Capture CCEL event log.
#
# Two artifacts are needed to reconstruct the measurement chain offline:
#   - CCEL           : the small ACPI table (pointer+length into the log region)
#   - data/CCEL      : the actual CC event log blob (TCG_PCR_EVENT2 records) that
#                      ccel_replay.py parses and replays to reproduce RTMR0-3.
# Older kernels only expose the table; the data blob lives under tables/data/.
echo "Capturing CCEL..."
xxd /sys/firmware/acpi/tables/CCEL > "$OUTPUT_DIR/ccel.txt" 2>/dev/null || true
cp /sys/firmware/acpi/tables/CCEL      "$OUTPUT_DIR/ccel.bin"      2>/dev/null || true
if [ -r /sys/firmware/acpi/tables/data/CCEL ]; then
    cp /sys/firmware/acpi/tables/data/CCEL "$OUTPUT_DIR/ccel_data.bin" 2>/dev/null || true
    echo "  Captured event-log data blob: $OUTPUT_DIR/ccel_data.bin ($(stat -c%s "$OUTPUT_DIR/ccel_data.bin" 2>/dev/null || echo 0) bytes)"
else
    echo "  WARNING: /sys/firmware/acpi/tables/data/CCEL not readable — event-log replay will be unavailable."
fi

# Capture the fw_cfg blobs and SMBIOS tables that the RTMR0 "ACPI DATA" (events
# #11-13) and SMBIOS handoff (event #14) digests are computed over. These are the
# raw preimages: each RTMR0 ACPI/SMBIOS digest is SHA-384 of the corresponding
# blob below, so capturing them lets the offline generator reproduce (and the
# matcher reverse-engineer) those events without booting the topology again.
echo "Capturing fw_cfg ACPI + SMBIOS preimages..."
FWCFG="/sys/firmware/qemu_fw_cfg/by_name"
declare -A FWCFG_ITEMS=(
    [etc/acpi/tables]=acpi_tables.bin        # -> RTMR0 event #13
    [etc/table-loader]=table_loader.bin      # -> RTMR0 event #11
    [etc/acpi/rsdp]=rsdp.bin                 # -> RTMR0 event #12
    [etc/smbios/smbios-tables]=smbios_tables.bin   # SMBIOS structure table (#14 candidate)
    [etc/smbios/smbios-anchor]=smbios_anchor.bin   # SMBIOS entry point   (#14 candidate)
)
for item in "${!FWCFG_ITEMS[@]}"; do
    if [ -r "$FWCFG/$item/raw" ]; then
        cp "$FWCFG/$item/raw" "$OUTPUT_DIR/${FWCFG_ITEMS[$item]}" 2>/dev/null || true
        echo "  fw_cfg $item -> ${FWCFG_ITEMS[$item]} ($(stat -c%s "$OUTPUT_DIR/${FWCFG_ITEMS[$item]}" 2>/dev/null || echo 0) bytes)"
    else
        echo "  (fw_cfg $item not readable)"
    fi
done
# The installed SMBIOS as the kernel sees it (alternative #14 preimage candidates).
for f in /sys/firmware/dmi/tables/DMI /sys/firmware/dmi/tables/smbios_entry_point; do
    if [ -r "$f" ]; then
        cp "$f" "$OUTPUT_DIR/$(basename "$f")" 2>/dev/null || true
        echo "  $f -> $(basename "$f") ($(stat -c%s "$OUTPUT_DIR/$(basename "$f")" 2>/dev/null || echo 0) bytes)"
    fi
done

echo ""
echo "Snapshot saved to $OUTPUT_DIR/"
echo "Files created:"
ls -lh "$OUTPUT_DIR/"
echo ""

# Extract RTMR values
echo "RTMR values:"
cd "$OUTPUT_DIR"
../extract-tdx-quote --json > rtmrs.json 2>&1
cd - > /dev/null
grep -i "rtmr" "$OUTPUT_DIR/rtmrs.json" || echo "Failed to extract RTMRs"
echo ""

if [ $QUOTE_EXIT -ne 0 ]; then
    echo "WARNING: Quote generation may have failed (exit code: $QUOTE_EXIT)"
fi

echo "Capture complete for Boot $BOOT_NUM"