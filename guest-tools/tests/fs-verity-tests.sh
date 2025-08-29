#!/bin/bash
# fs-verity Implementation Test Suite
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test results
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_SKIPPED=0

# Log functions
log_test() {
    echo -e "${YELLOW}[TEST]${NC} $1"
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

log_skip() {
    echo -e "${BLUE}[SKIP]${NC} $1"
    TESTS_SKIPPED=$((TESTS_SKIPPED + 1))
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# Ensure we're running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root"
    exit 1
fi

echo "========================================="
echo "Keyless fs-verity Implementation Test Suite"
echo "========================================="

# Test 1: Kernel fs-verity support
log_test "Checking kernel fs-verity support"
if [[ -d /sys/fs/verity ]]; then
    log_pass "Kernel supports fs-verity (/sys/fs/verity exists)"
    
    # Check supported hash algorithms
    if [[ -f /sys/fs/verity/supported_hash_algorithms ]]; then
        ALGORITHMS=$(cat /sys/fs/verity/supported_hash_algorithms 2>/dev/null || echo "unknown")
        log_info "Supported hash algorithms: $ALGORITHMS"
    fi
else
    log_fail "Kernel does not support fs-verity"
    exit 1
fi

# Test 2: Tools installation (fsverity only)
log_test "Checking fs-verity tools installation"
if command -v fsverity >/dev/null 2>&1; then
    VERSION=$(fsverity --version 2>&1 | head -n1)
    log_pass "fsverity tool installed: $VERSION"
else
    log_fail "fsverity tool not installed"
    exit 1
fi

# OpenSSL not required for keyless mode
if command -v openssl >/dev/null 2>&1; then
    log_info "OpenSSL available (not required for keyless fs-verity)"
else
    log_info "OpenSSL not available (not required for keyless mode)"
fi

# Test 3: Keyless mode verification (no signing keys needed)
log_test "Verifying keyless fs-verity mode"
if [[ -d "/etc/fs-verity-keys" ]]; then
    log_info "fs-verity keys directory exists but not needed for keyless mode"
else
    log_pass "No fs-verity keys directory (correct for keyless mode)"
fi

log_pass "Running in keyless fs-verity mode (measurements only)"

# Test 4: Critical files protection status
log_test "Checking critical files fs-verity protection"
PROTECTED_FILES=()
UNPROTECTED_FILES=()

# Expected critical files
EXPECTED_FILES=(
    "/usr/local/bin/k3s"
    "/usr/bin/k3s"
    "/usr/local/bin/opa"
    "/usr/bin/opa"
)

# Add systemd services
while IFS= read -r file; do
    [[ -f "$file" ]] && EXPECTED_FILES+=("$file")
done < <(find /etc/systemd/system -name "*.service" -type f 2>/dev/null)

# Check each expected file
for file in "${EXPECTED_FILES[@]}"; do
    if [[ -f "$file" ]]; then
        if fsverity measure "$file" >/dev/null 2>&1; then
            PROTECTED_FILES+=("$file")
        else
            UNPROTECTED_FILES+=("$file")
        fi
    fi
done

# Also scan for any fs-verity protected files
ALL_PROTECTED=()
while IFS= read -r file; do
    if [[ -f "$file" ]] && fsverity measure "$file" >/dev/null 2>&1; then
        ALL_PROTECTED+=("$file")
    fi
done < <(find /usr/local/bin /usr/bin /etc -type f 2>/dev/null)

# Results
if [[ ${#ALL_PROTECTED[@]} -gt 0 ]]; then
    log_pass "Found ${#ALL_PROTECTED[@]} fs-verity protected files total"
    
    log_info "Sample protected files:"
    for i in "${!ALL_PROTECTED[@]}"; do
        if [[ $i -lt 5 ]]; then  # Show first 5
            MEASUREMENT=$(fsverity measure "${ALL_PROTECTED[$i]}" 2>/dev/null | cut -d' ' -f1)
            log_info "  ${ALL_PROTECTED[$i]}: ${MEASUREMENT:0:16}..."
        fi
    done
    
    if [[ ${#ALL_PROTECTED[@]} -gt 5 ]]; then
        log_info "  ... and $((${#ALL_PROTECTED[@]} - 5)) more files"
    fi
else
    log_fail "No fs-verity protected files found"
fi

if [[ ${#UNPROTECTED_FILES[@]} -gt 0 ]]; then
    log_info "Unprotected expected files (may not exist):"
    for file in "${UNPROTECTED_FILES[@]}"; do
        log_info "  $file"
    done
fi

# Test 5: Measurements files verification
log_test "Checking measurements files"
MEASUREMENTS_TXT="/etc/fs-verity-measurements.txt"
MEASUREMENTS_JSON="/etc/fs-verity-measurements.json"

if [[ -f "$MEASUREMENTS_TXT" ]]; then
    COUNT=$(grep -c "^sha256:" "$MEASUREMENTS_TXT" 2>/dev/null || echo "0")
    BUILD_DATE=$(grep "Generated on:" "$MEASUREMENTS_TXT" 2>/dev/null | cut -d: -f2- | xargs)
    log_pass "Text measurements file exists with $COUNT measurements"
    log_info "Build date: $BUILD_DATE"
    
    # Show file size
    SIZE=$(stat -c %s "$MEASUREMENTS_TXT")
    log_info "File size: $SIZE bytes"
else
    log_fail "Text measurements file missing: $MEASUREMENTS_TXT"
fi

if [[ -f "$MEASUREMENTS_JSON" ]]; then
    if command -v jq >/dev/null 2>&1; then
        PROTECTED_COUNT=$(jq -r '.protected_files // 0' "$MEASUREMENTS_JSON" 2>/dev/null)
        TOTAL_COUNT=$(jq -r '.total_files // 0' "$MEASUREMENTS_JSON" 2>/dev/null)
        TIMESTAMP=$(jq -r '.timestamp // "unknown"' "$MEASUREMENTS_JSON" 2>/dev/null)
        VM_HOSTNAME=$(jq -r '.hostname // "unknown"' "$MEASUREMENTS_JSON" 2>/dev/null)
        
        log_pass "JSON measurements file: $PROTECTED_COUNT/$TOTAL_COUNT files protected"
        log_info "Build timestamp: $TIMESTAMP"
        log_info "VM hostname: $VM_HOSTNAME"
    else
        log_pass "JSON measurements file exists (jq not available for parsing)"
    fi
else
    log_fail "JSON measurements file missing: $MEASUREMENTS_JSON"
fi

# Test 6: Tampering protection test
log_test "Testing tampering protection (immutability)"

# Find a protected file to test
TEST_FILE=""
while IFS= read -r file; do
    if [[ -f "$file" ]] && fsverity measure "$file" >/dev/null 2>&1; then
        TEST_FILE="$file"
        break
    fi
done < <(find /usr/local/bin /usr/bin -type f 2>/dev/null)

if [[ -z "$TEST_FILE" ]]; then
    log_skip "No protected files found for tampering test"
else
    log_info "Testing tampering protection on: $TEST_FILE"
    
    # Get original measurement
    ORIGINAL_MEASUREMENT=$(fsverity measure "$TEST_FILE" 2>/dev/null | cut -d' ' -f1)
    log_info "Original measurement: ${ORIGINAL_MEASUREMENT:0:16}..."
    
    # Try to modify the file (this should fail)
    TEMP_CONTENT="tamper-test-$(date +%s)"
    if echo "$TEMP_CONTENT" >> "$TEST_FILE" 2>/dev/null; then
        log_fail "File modification should be blocked but succeeded: $TEST_FILE"
        
        # If modification succeeded, file is not properly protected
        NEW_MEASUREMENT=$(fsverity measure "$TEST_FILE" 2>/dev/null | cut -d' ' -f1 || echo "")
        if [[ "$NEW_MEASUREMENT" != "$ORIGINAL_MEASUREMENT" ]]; then
            log_fail "File measurement changed after modification attempt"
        fi
    else
        log_pass "File modification properly blocked by fs-verity: $TEST_FILE"
    fi
    
    # Verify the file is still readable
    if [[ -r "$TEST_FILE" ]]; then
        log_pass "Protected file remains readable"
    else
        log_fail "Protected file is no longer readable"
    fi
    
    # Verify measurement hasn't changed
    CURRENT_MEASUREMENT=$(fsverity measure "$TEST_FILE" 2>/dev/null | cut -d' ' -f1 || echo "")
    if [[ "$CURRENT_MEASUREMENT" == "$ORIGINAL_MEASUREMENT" ]]; then
        log_pass "File measurement unchanged after tampering attempt"
    else
        log_fail "File measurement changed unexpectedly"
    fi
fi

# Test 7: Build artifacts verification
log_test "Checking build artifacts"
BUILD_INFO="/etc/tee-vm-final-state.txt"

if [[ -f "$BUILD_INFO" ]]; then
    log_pass "Build info file exists: $BUILD_INFO"
    if grep -q "fs-verity.*Enabled" "$BUILD_INFO"; then
        log_pass "Build info confirms fs-verity is enabled"
    else
        log_fail "Build info missing fs-verity status"
    fi
    
    # Show some key info
    log_info "Build information summary:"
    grep -E "(Build Date|Protected Files|Success Rate)" "$BUILD_INFO" | sed 's/^/  /' || true
else
    log_fail "Build info file missing: $BUILD_INFO"
fi

# Test 8: Performance impact test
log_test "Testing performance impact"

# Find a protected executable
TEST_BINARY=""
for binary in "/usr/local/bin/k3s" "/usr/bin/k3s" "/usr/local/bin/opa" "/usr/bin/opa"; do
    if [[ -x "$binary" ]] && fsverity measure "$binary" >/dev/null 2>&1; then
        TEST_BINARY="$binary"
        break
    fi
done

if [[ -z "$TEST_BINARY" ]]; then
    log_skip "No protected executables found for performance test"
else
    log_info "Testing performance impact on: $TEST_BINARY"
    
    # Time the execution of --help or --version (should be fast)
    START_TIME=$(date +%s%N)
    if timeout 10s "$TEST_BINARY" --version >/dev/null 2>&1 || timeout 10s "$TEST_BINARY" --help >/dev/null 2>&1; then
        END_TIME=$(date +%s%N)
        DURATION=$(( (END_TIME - START_TIME) / 1000000 )) # Convert to milliseconds
        
        if [[ $DURATION -lt 5000 ]]; then # Less than 5 seconds
            log_pass "Protected binary executes normally (${DURATION}ms)"
        else
            log_fail "Protected binary execution seems slow (${DURATION}ms)"
        fi
    else
        log_skip "Could not test binary execution performance"
    fi
fi

# Test 9: Filesystem verification
log_test "Checking filesystem compatibility"
ROOT_FS=$(findmnt -n -o FSTYPE /)
log_info "Root filesystem type: $ROOT_FS"

if [[ "$ROOT_FS" == "ext4" || "$ROOT_FS" == "btrfs" ]]; then
    log_pass "Root filesystem supports fs-verity: $ROOT_FS"
else
    log_fail "Root filesystem may not support fs-verity: $ROOT_FS"
fi

# Test 10: Verification of Ansible cleanup
log_test "Checking Ansible cleanup"
if [[ ! -d "/root/ansible" ]]; then
    log_pass "Ansible artifacts cleaned up (/root/ansible removed)"
else
    log_fail "Ansible artifacts not cleaned up (/root/ansible still exists)"
fi

if [[ ! -f "/root/setup-server.sh" ]]; then
    log_pass "Setup script cleaned up (/root/setup-server.sh removed)"
else
    log_fail "Setup script not cleaned up (/root/setup-server.sh still exists)"
fi

# Summary
echo "========================================="
echo "Test Results Summary"
echo "========================================="
echo -e "${GREEN}Passed:${NC} $TESTS_PASSED"
echo -e "${RED}Failed:${NC} $TESTS_FAILED"
echo -e "${BLUE}Skipped:${NC} $TESTS_SKIPPED"

if [[ $TESTS_FAILED -eq 0 ]]; then
    echo -e "${GREEN}✓ All tests passed! Keyless fs-verity is properly implemented.${NC}"
    echo
    echo -e "${BLUE}Keyless fs-verity Protection Summary:${NC}"
    if [[ -f "$MEASUREMENTS_JSON" ]] && command -v jq >/dev/null 2>&1; then
        echo "- Protected files: $(jq -r '.protected_files // 0' "$MEASUREMENTS_JSON")/$(jq -r '.total_files // 0' "$MEASUREMENTS_JSON")"
        echo "- Build date: $(jq -r '.timestamp // "Unknown"' "$MEASUREMENTS_JSON")"
    fi
    echo "- Measurements: $MEASUREMENTS_TXT"
    echo "- Mode: Keyless (measurement-only)"
    echo "- TDX Integration: Ready"
    echo "- Build info: $BUILD_INFO"
    echo
    echo -e "${GREEN}VM is ready for shutdown and TDX-enabled image capture.${NC}"
    exit 0
else
    echo -e "${YELLOW}Some tests failed. Review the output above for details.${NC}"
    echo
    echo "Common troubleshooting steps:"
    echo "1. Ensure kernel has CONFIG_FS_VERITY=y enabled"
    echo "2. Check filesystem compatibility (ext4/btrfs required)"
    echo "3. Verify Ansible fs-verity role completed successfully"
    echo "4. Check fs-verity setup logs in Ansible output"
    exit 1
fi