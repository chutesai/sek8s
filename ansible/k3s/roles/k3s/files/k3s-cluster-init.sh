#!/bin/bash
# /usr/local/bin/k3s-cluster-init.sh
# k3s-cluster-init: Run multiple cluster initialization scripts with individual tracking
set -e

# Configuration
SCRIPT_DIR="${SCRIPT_DIR:-/usr/local/bin/k3s-init-scripts}"
MARKER_DIR="${MARKER_DIR:-/var/lib/rancher/k3s/init-markers}"
LOG_FILE="${LOG_FILE:-/var/log/k3s-cluster-init.log}"
MAX_SCRIPT_TIMEOUT="${MAX_SCRIPT_TIMEOUT:-300}"  # 5 minutes per script

# Ensure directories exist
mkdir -p "$MARKER_DIR"
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Function to notify systemd we're still alive
notify_systemd() {
    if [ -n "$NOTIFY_SOCKET" ]; then
        systemd-notify --status="$1" || true
    fi
}

# Function to send watchdog keepalive
send_watchdog() {
    if [ -n "$NOTIFY_SOCKET" ]; then
        systemd-notify WATCHDOG=1 || true
    fi
}

# Function to check if a script has already completed successfully
is_script_completed() {
    local script_name="$1"
    local marker_file="$MARKER_DIR/${script_name}.completed"
    
    if [ -f "$marker_file" ]; then
        return 0  # Already completed
    fi
    
    return 1  # Not completed
}

# Function to mark a script as completed
mark_script_completed() {
    local script_name="$1"
    local marker_file="$MARKER_DIR/${script_name}.completed"
    
    touch "$marker_file"
    log "Marked script $script_name as completed"
}

# Function to mark a script as failed
mark_script_failed() {
    local script_name="$1"
    local exit_code="$2"
    local marker_file="$MARKER_DIR/${script_name}.failed"
    
    echo "exit_code=$exit_code" > "$marker_file"
    echo "timestamp=$(date '+%Y-%m-%d %H:%M:%S')" >> "$marker_file"
    log "Marked script $script_name as failed with exit code $exit_code"
}

# Function to run a single script with timeout and error handling
run_script() {
    local script_path="$1"
    local script_name=$(basename "$script_path")
    
    log "Starting execution of script: $script_name"
    notify_systemd "Running $script_name"
    
    # Check if already completed
    if is_script_completed "$script_name"; then
        log "Script $script_name already completed successfully, skipping"
        return 0
    fi
    
    # Remove any old failure markers
    rm -f "$MARKER_DIR/${script_name}.failed"
    
    # Create a temporary log file for this script
    local script_log="/tmp/${script_name}.log"
    
    # Run the script with timeout
    local exit_code=0
    local start_time=$(date +%s)
    
    log "Executing: $script_path (timeout: ${MAX_SCRIPT_TIMEOUT}s)"
    
    if timeout "$MAX_SCRIPT_TIMEOUT" bash "$script_path" > "$script_log" 2>&1; then
        local end_time=$(date +%s)
        local duration=$((end_time - start_time))
        
        log "Script $script_name completed successfully in ${duration}s"
        mark_script_completed "$script_name"
        
        # Show last few lines of output for context
        if [ -s "$script_log" ]; then
            log "Last 5 lines of output from $script_name:"
            tail -5 "$script_log" | while read line; do
                log "  $script_name: $line"
            done
        fi
        
        rm -f "$script_log"
        return 0
    else
        exit_code=$?
        local end_time=$(date +%s)
        local duration=$((end_time - start_time))
        
        if [ $exit_code -eq 124 ]; then
            log "ERROR: Script $script_name timed out after ${MAX_SCRIPT_TIMEOUT}s"
        else
            log "ERROR: Script $script_name failed with exit code $exit_code after ${duration}s"
        fi
        
        mark_script_failed "$script_name" "$exit_code"
        
        # Show script output for debugging
        if [ -s "$script_log" ]; then
            log "Output from failed script $script_name:"
            cat "$script_log" | while read line; do
                log "  $script_name: $line"
            done
        fi
        
        rm -f "$script_log"
        return $exit_code
    fi
}

# Function to wait for k3s to be ready
wait_for_k3s() {
    local max_attempts=60
    local attempt=1
    
    log "Waiting for k3s to be ready..."
    notify_systemd "Waiting for k3s API"
    
    while [ $attempt -le $max_attempts ]; do
        # Check if k3s service is running
        if ! systemctl is-active --quiet k3s; then
            log "k3s service is not active, waiting..."
            sleep 5
            ((attempt += 5))
            continue
        fi
        
        # Check basic API connectivity
        if kubectl get --raw='/readyz' >/dev/null 2>&1; then
            log "API server readiness check passed"
            return 0
        fi
        
        # Send watchdog keepalive
        systemd-notify WATCHDOG=1 || true
        
        if [ $((attempt % 15)) -eq 0 ]; then
            log "Still waiting for API server readiness... ($attempt/$max_attempts)"
        fi
        
        sleep 2
        ((attempt++))
    done
    
    log "ERROR: API server not ready after $max_attempts attempts"
    return 1
}

# Function to discover and sort scripts
get_script_list() {
    if [ ! -d "$SCRIPT_DIR" ]; then
        log "Script directory $SCRIPT_DIR does not exist"
        return 1
    fi
    
    # Find all executable shell scripts, sort them naturally
    find "$SCRIPT_DIR" -name "*.sh" -type f -executable | sort -V
}

# Main execution
main() {
    log "Starting k3s cluster initialization"
    log "Script directory: $SCRIPT_DIR"
    log "Marker directory: $MARKER_DIR"
    log "Max script timeout: ${MAX_SCRIPT_TIMEOUT}s"
    
    notify_systemd "Initializing cluster scripts"
    
    # Wait for k3s to be ready first
    if ! wait_for_k3s; then
        log "FATAL: k3s is not ready, cannot proceed with initialization"
        notify_systemd "ERROR: k3s not ready"
        exit 1
    fi
    
    # Get list of scripts to run
    local scripts
    if ! scripts=$(get_script_list); then
        log "FATAL: Could not get script list"
        notify_systemd "ERROR: No scripts found"
        exit 1
    fi
    
    if [ -z "$scripts" ]; then
        log "No scripts found in $SCRIPT_DIR, initialization complete"
        notify_systemd "No scripts to run"
        systemd-notify --ready
        exit 0
    fi
    
    # Count scripts for progress tracking
    local total_scripts=$(echo "$scripts" | wc -l)
    local current_script=0
    local successful_scripts=0
    local failed_scripts=0
    
    log "Found $total_scripts script(s) to process"
    
    # Process each script
    while IFS= read -r script_path; do
        ((current_script++))
        local script_name=$(basename "$script_path")
        
        log "Processing script $current_script/$total_scripts: $script_name"
        notify_systemd "Script $current_script/$total_scripts: $script_name"
        
        send_watchdog
        
        if run_script "$script_path"; then
            ((successful_scripts++))
            log "✓ Script $script_name completed successfully"
        else
            ((failed_scripts++))
            log "✗ Script $script_name failed (continuing with remaining scripts)"
        fi
        
        send_watchdog
        
        # Brief pause between scripts
        sleep 2
    done <<< "$scripts"
    
    # Final summary
    log "=== Cluster Initialization Summary ==="
    log "Total scripts: $total_scripts"
    log "Successful: $successful_scripts"
    log "Failed: $failed_scripts"
    log "====================================="
    
    if [ $failed_scripts -eq 0 ]; then
        log "All scripts completed successfully"
        notify_systemd "All scripts completed successfully"
        systemd-notify --ready
        exit 0
    else
        log "Some scripts failed, but initialization completed"
        notify_systemd "Completed with $failed_scripts failures"
        systemd-notify --ready
        # Exit 0 because we want the service to stay running even if some scripts failed
        exit 0
    fi
}

# Handle signals gracefully
trap 'log "Received shutdown signal, exiting..."; exit 0' TERM INT

# Run main function
main "$@"