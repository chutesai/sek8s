#!/bin/bash
# rtmr_capture.sh - Capture RTMR values and system state

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

# Capture CCEL event log
echo "Capturing CCEL..."
xxd /sys/firmware/acpi/tables/CCEL > "$OUTPUT_DIR/ccel.txt" 2>/dev/null || true

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