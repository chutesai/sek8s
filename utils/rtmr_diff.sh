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
        
        # Compare RTMRs
        echo "### RTMR Comparison ###"
        if diff -q "$BOOT1/rtmrs.json" "$BOOT2/rtmrs.json" > /dev/null 2>&1; then
            echo "✓ RTMRs are IDENTICAL"
        else
            echo "✗ RTMRs DIFFER:"
            diff "$BOOT1/rtmrs.json" "$BOOT2/rtmrs.json" || true
        fi
        echo ""
        
        # Compare binary quotes if they exist
        if [ -f "$BOOT1/quote.bin" ] && [ -f "$BOOT2/quote.bin" ]; then
            echo "### Binary Quote Comparison ###"
            if cmp -s "$BOOT1/quote.bin" "$BOOT2/quote.bin"; then
                echo "✓ Binary quotes are IDENTICAL"
            else
                echo "✗ Binary quotes DIFFER (expected due to nonces)"
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
        
        # Compare UEFI variables
        echo "### UEFI Variables ###"
        VARS_TO_CHECK=("BootCurrent" "BootOrder" "MTC" "NvVars" "VarErrorFlag")
        for var in "${VARS_TO_CHECK[@]}"; do
            if [ -f "$BOOT1/efivar_${var}.txt" ] && [ -f "$BOOT2/efivar_${var}.txt" ]; then
                if diff -q "$BOOT1/efivar_${var}.txt" "$BOOT2/efivar_${var}.txt" > /dev/null 2>&1; then
                    echo "  ✓ $var: IDENTICAL"
                else
                    echo "  ✗ $var: DIFFERS"
                    diff "$BOOT1/efivar_${var}.txt" "$BOOT2/efivar_${var}.txt" | head -10 || true
                fi
            fi
        done
        echo ""
        
        # Compare CCEL
        echo "### CCEL Event Log ###"
        if [ -f "$BOOT1/ccel.txt" ] && [ -f "$BOOT2/ccel.txt" ]; then
            if diff -q "$BOOT1/ccel.txt" "$BOOT2/ccel.txt" > /dev/null 2>&1; then
                echo "✓ CCEL is IDENTICAL"
            else
                echo "✗ CCEL DIFFERS (first 20 lines of diff):"
                diff "$BOOT1/ccel.txt" "$BOOT2/ccel.txt" | head -20 || true
            fi
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
    
    echo "### RTMR Values Across All Boots ###"
    for snapshot in "${SNAPSHOTS[@]}"; do
        echo ""
        echo "$snapshot:"
        if [ -f "$snapshot/rtmrs.json" ]; then
            grep "RTMR" "$snapshot/rtmrs.json" | head -5 || echo "  (cannot parse RTMRs)"
        else
            echo "  (no RTMR data found)"
        fi
    done
    echo ""
    
    if [ ${#SNAPSHOTS[@]} -gt 2 ]; then
        echo "### Are all RTMRs identical? ###"
        FIRST_RTMR="${SNAPSHOTS[0]}/rtmrs.json"
        ALL_IDENTICAL=true
        
        for ((i=1; i<${#SNAPSHOTS[@]}; i++)); do
            if ! diff -q "$FIRST_RTMR" "${SNAPSHOTS[$i]}/rtmrs.json" > /dev/null 2>&1; then
                ALL_IDENTICAL=false
                break
            fi
        done
        
        if $ALL_IDENTICAL; then
            echo "✓ ALL RTMRs are identical across all boots"
        else
            echo "✗ RTMRs differ between boots"
            echo ""
            echo "Boot 1 is different: $([ -f "$FIRST_RTMR" ] && echo "Yes" || echo "Unknown")"
            
            # Check if Boot 2+ are consistent
            if [ ${#SNAPSHOTS[@]} -gt 2 ]; then
                BOOT2_RTMR="${SNAPSHOTS[1]}/rtmrs.json"
                SUBSEQUENT_IDENTICAL=true
                for ((i=2; i<${#SNAPSHOTS[@]}; i++)); do
                    if ! diff -q "$BOOT2_RTMR" "${SNAPSHOTS[$i]}/rtmrs.json" > /dev/null 2>&1; then
                        SUBSEQUENT_IDENTICAL=false
                        break
                    fi
                done
                
                if $SUBSEQUENT_IDENTICAL; then
                    echo "✓ Boot 2+ are consistent with each other (but different from Boot 1)"
                else
                    echo "✗ Even subsequent boots differ from each other"
                fi
            fi
        fi
    fi
    
} | tee "$DIFF_REPORT"

echo ""
echo "==================================="
echo "Full report saved to: $DIFF_REPORT"
echo "==================================="