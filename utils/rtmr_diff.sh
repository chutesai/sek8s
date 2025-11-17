#!/bin/bash
# rtmr_diff.sh - Compare RTMR snapshots across boots

BASE_DIR="rtmr_snapshots"
DIFF_REPORT="rtmr_diff_report_$(date +%Y%m%d_%H%M%S).txt"

echo "==================================="
echo "RTMR Snapshot Comparison Report"
echo "Generated: $(date)"
echo "==================================="
echo ""

# Find all snapshot directories
SNAPSHOTS=($(ls -d ${BASE_DIR}_boot* 2>/dev/null | sort -V))

if [ ${#SNAPSHOTS[@]} -lt 2 ]; then
    echo "ERROR: Need at least 2 snapshots to compare"
    echo "Found: ${#SNAPSHOTS[@]} snapshot(s)"
    exit 1
fi

echo "Found ${#SNAPSHOTS[@]} snapshots:"
for snapshot in "${SNAPSHOTS[@]}"; do
    echo "  - $snapshot"
done
echo ""

# Create diff report
{
    echo "==================================="
    echo "RTMR Snapshot Comparison Report"
    echo "Generated: $(date)"
    echo "==================================="
    echo ""
    echo "Snapshots compared: ${#SNAPSHOTS[@]}"
    for snapshot in "${SNAPSHOTS[@]}"; do
        echo "  - $snapshot (captured: $(cat $snapshot/timestamp.txt 2>/dev/null || echo 'unknown'))"
    done
    echo ""
    echo "==================================="
    
    # Compare each consecutive pair
    for ((i=0; i<${#SNAPSHOTS[@]}-1; i++)); do
        BOOT1="${SNAPSHOTS[$i]}"
        BOOT2="${SNAPSHOTS[$((i+1))]}"
        
        echo ""
        echo "-----------------------------------"
        echo "Comparing: $BOOT1 vs $BOOT2"
        echo "-----------------------------------"
        echo ""
        
        # Compare quotes
        echo "### Quote Text Comparison ###"
        if diff -q "$BOOT1/rtmrs.json" "$BOOT2/rtmrs.json" > /dev/null 2>&1; then
            echo "✓ Quote outputs are IDENTICAL"
        else
            echo "✗ Quote outputs DIFFER:"
            diff "$BOOT1/rtmrs.json" "$BOOT2/rtmrs.json" || true
        fi
        echo ""
        
        # Compare binary quotes if they exist
        if [ -f "$BOOT1/quote.bin" ] && [ -f "$BOOT2/quote.bin" ]; then
            echo "### Binary Quote Comparison ###"
            if cmp -s "$BOOT1/quote.bin" "$BOOT2/quote.bin"; then
                echo "✓ Binary quotes are IDENTICAL"
            else
                echo "✗ Binary quotes DIFFER"
            fi
            echo ""
        fi
        
        # Compare kernel cmdline
        echo "### Kernel Command Line ###"
        if diff -q "$BOOT1/cmdline.txt" "$BOOT2/cmdline.txt" > /dev/null 2>&1; then
            echo "✓ Kernel cmdline is IDENTICAL"
        else
            echo "✗ Kernel cmdline DIFFERS:"
            diff "$BOOT1/cmdline.txt" "$BOOT2/cmdline.txt" || true
        fi
        echo ""
        
        # Compare early dmesg for INITRD addresses
        echo "### Early Boot (dmesg) ###"
        echo "INITRD address comparison:"
        grep "INITRD=" "$BOOT1/dmesg.txt" || echo "  Not found in $BOOT1"
        grep "INITRD=" "$BOOT2/dmesg.txt" || echo "  Not found in $BOOT2"
        echo ""
    done
    
    # Summary comparison of all boots
    echo ""
    echo "==================================="
    echo "Summary: All Boots Comparison"
    echo "==================================="
    echo ""
    
    if [ ${#SNAPSHOTS[@]} -gt 2 ]; then
        echo "### Are all quotes identical? ###"
        FIRST_QUOTE="${SNAPSHOTS[0]}/quote.txt"
        ALL_IDENTICAL=true
        
        for ((i=1; i<${#SNAPSHOTS[@]}; i++)); do
            if ! diff -q "$FIRST_QUOTE" "${SNAPSHOTS[$i]}/quote.txt" > /dev/null 2>&1; then
                ALL_IDENTICAL=false
                break
            fi
        done
        
        if $ALL_IDENTICAL; then
            echo "✓ ALL quotes are identical across all boots"
        else
            echo "✗ Quotes differ between boots"
            echo ""
            echo "Per-boot comparison:"
            for snapshot in "${SNAPSHOTS[@]}"; do
                echo "  $snapshot:"
                grep -i "rtmr" "$snapshot/rtmrs.json" | head -5 || echo "    (no RTMR data found)"
            done
        fi
    fi
    
} | tee "$DIFF_REPORT"

echo ""
echo "==================================="
echo "Full report saved to: $DIFF_REPORT"
echo "==================================="