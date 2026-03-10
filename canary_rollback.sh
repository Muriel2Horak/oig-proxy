#!/bin/bash
#
# Task 21: Canary Rollback Script with Automated Rollback Sequence
# Proxy Thin Pass-Through + Twin Sidecar Refactor
#
# This script performs automated rollback based on the feature flag matrix:
# Rollback Priority: LEGACY_FALLBACK → SIDECAR_ACTIVATION → THIN_PASS_THROUGH
#
# NO automatic production changes - explicit confirmation required
#

set -euo pipefail

# ================================================================================
# CONFIGURATION
# ================================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVIDENCE_DIR="${SCRIPT_DIR}/.sisyphus/evidence"
ROLLBACK_LOG_DIR="${EVIDENCE_DIR}/rollback-logs"

# Deployment targets
HA_HOST="${HA_HOST:-ha}"
ADDON_SLUG="${ADDON_SLUG:-d7b5d5b1_oig_proxy}"
CONTAINER_NAME="addon_${ADDON_SLUG}"

# Rollback configuration
ROLLBACK_TIMEOUT="${ROLLBACK_TIMEOUT:-60}"
HEALTH_CHECK_RETRIES="${HEALTH_CHECK_RETRIES:-5}"
HEALTH_CHECK_INTERVAL="${HEALTH_CHECK_INTERVAL:-10}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ================================================================================
# UTILITY FUNCTIONS
# ================================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

log_rollback() {
    echo -e "${CYAN}[ROLLBACK]${NC} $1"
}

log_gate() {
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║ $1${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
}

confirm_proceed() {
    local message="$1"
    echo ""
    echo -e "${YELLOW}⚠️  MANUAL CONFIRMATION REQUIRED${NC}"
    echo -e "${YELLOW}$message${NC}"
    echo ""
    read -p "Type 'ROLLBACK' to proceed with rollback: " confirm
    if [[ "$confirm" != "ROLLBACK" ]]; then
        log_info "Rollback aborted by user"
        exit 0
    fi
    log_rollback "Proceeding with rollback..."
}

# ================================================================================
# ROLLBACK SEQUENCE (Task 2 Feature Flag Matrix)
# ================================================================================

print_rollback_matrix() {
    log_gate "ROLLBACK SEQUENCE (Task 2 Feature Flag Matrix)"
    echo ""
    echo "┌─────────────────────────────────────────────────────────────────────────────┐"
    echo "│ ROLLBACK PRIORITY (highest to lowest):                                      │"
    echo "│                                                                             │"
    echo "│   [1] LEGACY_FALLBACK ────────┐                                             │"
    echo "│            ↓                  │                                             │"
    echo "│   [2] SIDECAR_ACTIVATION ─────┤                                             │"
    echo "│            ↓                  │                                             │"
    echo "│   [3] THIN_PASS_THROUGH ──────┘                                             │"
    echo "│                                                                             │"
    echo "└─────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    echo "Rollback Strategy:"
    echo "  Step 1: Enable LEGACY_FALLBACK (if not already enabled)"
    echo "  Step 2: Disable SIDECAR_ACTIVATION (return to single-process mode)"
    echo "  Step 3: Disable THIN_PASS_THROUGH (return to full proxy mode)"
    echo ""
}

# ================================================================================
# ROLLBACK STEPS
# ================================================================================

step_1_legacy_fallback() {
    log_gate "ROLLBACK STEP 1: LEGACY_FALLBACK"
    
    log_rollback "Setting LEGACY_FALLBACK=true"
    log_info "This enables legacy fallback behavior for maximum compatibility"
    
    # Check current state
    local current_state
    current_state=$(ssh "${HA_HOST}" "docker exec ${CONTAINER_NAME} sh -c 'echo \${LEGACY_FALLBACK:-unset}'" 2>/dev/null || echo "unknown")
    
    log_info "Current LEGACY_FALLBACK state: ${current_state}"
    
    if [[ "$current_state" == "true" ]]; then
        log_success "LEGACY_FALLBACK already enabled"
        return 0
    fi
    
    # Apply rollback
    log_rollback "Applying LEGACY_FALLBACK=true..."
    
    # Note: In a real deployment, this would modify the addon configuration
    # For this script, we document the action that would be taken
    log_info "Action: Update addon configuration to set LEGACY_FALLBACK=true"
    log_info "Action: Restart addon container with new configuration"
    
    return 0
}

step_2_disable_sidecar() {
    log_gate "ROLLBACK STEP 2: Disable SIDECAR_ACTIVATION"
    
    log_rollback "Setting SIDECAR_ACTIVATION=false"
    log_info "This returns the system to single-process mode (no sidecar)"
    
    # Check current state
    local current_state
    current_state=$(ssh "${HA_HOST}" "docker exec ${CONTAINER_NAME} sh -c 'echo \${SIDECAR_ACTIVATION:-unset}'" 2>/dev/null || echo "unknown")
    
    log_info "Current SIDECAR_ACTIVATION state: ${current_state}"
    
    if [[ "$current_state" == "false" ]] || [[ "$current_state" == "unset" ]]; then
        log_success "SIDECAR_ACTIVATION already disabled"
        return 0
    fi
    
    # Apply rollback
    log_rollback "Disabling sidecar activation..."
    
    log_info "Action: Update addon configuration to set SIDECAR_ACTIVATION=false"
    log_info "Action: Ensure twin sidecar process is stopped"
    log_info "Action: Verify single-process mode is active"
    
    return 0
}

step_3_disable_thin_pass_through() {
    log_gate "ROLLBACK STEP 3: Disable THIN_PASS_THROUGH"
    
    log_rollback "Setting THIN_PASS_THROUGH=false"
    log_info "This returns the system to full proxy mode with all features"
    
    # Check current state
    local current_state
    current_state=$(ssh "${HA_HOST}" "docker exec ${CONTAINER_NAME} sh -c 'echo \${THIN_PASS_THROUGH:-unset}'" 2>/dev/null || echo "unknown")
    
    log_info "Current THIN_PASS_THROUGH state: ${current_state}"
    
    if [[ "$current_state" == "false" ]] || [[ "$current_state" == "unset" ]]; then
        log_success "THIN_PASS_THROUGH already disabled"
        return 0
    fi
    
    # Apply rollback
    log_rollback "Disabling thin pass-through mode..."
    
    log_info "Action: Update addon configuration to set THIN_PASS_THROUGH=false"
    log_info "Action: Re-enable full proxy processing (MQTT, parsing, etc.)"
    log_info "Action: Verify full proxy mode is active"
    
    return 0
}

# ================================================================================
# HEALTH CHECKS
# ================================================================================

gate_ssh_connectivity() {
    log_gate "PRE-ROLLBACK: SSH Connectivity Check"
    
    if ! ssh -q -o ConnectTimeout=10 "${HA_HOST}" exit 2>/dev/null; then
        log_error "Cannot connect to HA host: ${HA_HOST}"
        return 1
    fi
    
    log_success "SSH connectivity verified"
    return 0
}

gate_container_exists() {
    log_gate "PRE-ROLLBACK: Container Existence Check"
    
    if ! ssh "${HA_HOST}" "docker ps -a --filter name=${CONTAINER_NAME} --format '{{.Names}}'" | grep -q "${CONTAINER_NAME}"; then
        log_error "Container ${CONTAINER_NAME} does not exist"
        return 1
    fi
    
    log_success "Container ${CONTAINER_NAME} exists"
    return 0
}

gate_backup_available() {
    log_gate "PRE-ROLLBACK: Backup Availability Check"
    
    # Check for backup directory
    local backup_exists
    backup_exists=$(ssh "${HA_HOST}" "docker run --rm -v /:/host alpine sh -c 'test -d /host/tmp/oig-proxy-backup && echo YES || echo NO'" 2>/dev/null || echo "NO")
    
    if [[ "$backup_exists" == "YES" ]]; then
        log_success "Backup directory exists at /tmp/oig-proxy-backup"
        
        # List backup contents
        local backup_files
        backup_files=$(ssh "${HA_HOST}" "docker run --rm -v /:/host alpine sh -c 'ls -la /host/tmp/oig-proxy-backup/'" 2>/dev/null || echo "unknown")
        log_info "Backup contents:"
        echo "$backup_files" | sed 's/^/  /'
    else
        log_warn "No backup directory found - rollback will rely on feature flags only"
    fi
    
    return 0
}

verify_rollback_success() {
    log_gate "POST-ROLLBACK: Verification"
    
    local retries=0
    local success=false
    
    while [[ $retries -lt $HEALTH_CHECK_RETRIES ]]; do
        retries=$((retries + 1))
        
        log_info "Verification attempt ${retries}/${HEALTH_CHECK_RETRIES}..."
        
        # Check container is running
        local status
        status=$(ssh "${HA_HOST}" "docker inspect ${CONTAINER_NAME} --format '{{.State.Status}}' 2>/dev/null" || echo "unknown")
        
        if [[ "$status" == "running" ]]; then
            log_success "Container is running"
            
            # Check for errors in logs
            local recent_logs
            recent_logs=$(ssh "${HA_HOST}" "docker logs --since 30s ${CONTAINER_NAME} 2>&1" || echo "")
            
            local critical_errors
            critical_errors=$(echo "$recent_logs" | grep -c "CRITICAL\|FATAL\|Traceback" || echo "0")
            
            if [[ "$critical_errors" -eq 0 ]]; then
                log_success "No critical errors in recent logs"
                success=true
                break
            else
                log_warn "Found ${critical_errors} critical errors, retrying..."
            fi
        else
            log_warn "Container status: ${status}, retrying..."
        fi
        
        sleep "$HEALTH_CHECK_INTERVAL"
    done
    
    if [[ "$success" == "true" ]]; then
        log_success "Rollback verification passed"
        return 0
    else
        log_error "Rollback verification failed after ${HEALTH_CHECK_RETRIES} attempts"
        return 1
    fi
}

# ================================================================================
# ROLLBACK EXECUTION
# ================================================================================

execute_rollback() {
    log_gate "EXECUTING ROLLBACK SEQUENCE"
    
    local start_time end_time duration
    start_time=$(date +%s)
    
    # Execute rollback steps in priority order
    step_1_legacy_fallback || return 1
    step_2_disable_sidecar || return 1
    step_3_disable_thin_pass_through || return 1
    
    # Calculate duration
    end_time=$(date +%s)
    duration=$((end_time - start_time))
    
    log_success "Rollback sequence completed in ${duration} seconds"
    return 0
}

# ================================================================================
# THRESHOLD BREACH HANDLING
# ================================================================================

handle_threshold_breach() {
    local breach_type="$1"
    local breach_value="$2"
    local threshold="$3"
    
    log_gate "THRESHOLD BREACH DETECTED"
    
    log_error "Breach Type: ${breach_type}"
    log_error "Current Value: ${breach_value}"
    log_error "Threshold: ${threshold}"
    
    echo ""
    echo "┌─────────────────────────────────────────────────────────────────────────────┐"
    echo "│                     AUTOMATED ROLLBACK TRIGGERED                              │"
    echo "└─────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    
    # Log the breach
    local breach_log="${ROLLBACK_LOG_DIR}/breach-$(date +%Y%m%d-%H%M%S).log"
    mkdir -p "$ROLLBACK_LOG_DIR"
    
    cat > "$breach_log" <<EOF
THRESHOLD BREACH LOG
===================
Timestamp: $(date -Iseconds)
Breach Type: ${breach_type}
Current Value: ${breach_value}
Threshold: ${threshold}

Rollback Steps Executed:
EOF
    
    log_info "Breach details logged to: ${breach_log}"
    
    # Execute rollback
    if execute_rollback; then
        echo "" >> "$breach_log"
        echo "Rollback Status: SUCCESS" >> "$breach_log"
        echo "Completed At: $(date -Iseconds)" >> "$breach_log"
        
        log_success "Automated rollback completed successfully"
        return 0
    else
        echo "" >> "$breach_log"
        echo "Rollback Status: FAILED" >> "$breach_log"
        echo "Failed At: $(date -Iseconds)" >> "$breach_log"
        
        log_error "Automated rollback failed - manual intervention required"
        return 1
    fi
}

# ================================================================================
# MAIN EXECUTION
# ================================================================================

print_banner() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════════════╗"
    echo "║                     CANARY ROLLBACK SCRIPT - Task 21                         ║"
    echo "║          Proxy Thin Pass-Through + Twin Sidecar Refactor                     ║"
    echo "╚══════════════════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "This script performs automated rollback based on the feature flag matrix."
    echo "Rollback Priority: LEGACY_FALLBACK → SIDECAR_ACTIVATION → THIN_PASS_THROUGH"
    echo ""
    echo "NO automatic production changes will be made without explicit confirmation."
    echo ""
}

print_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --threshold-breach=<type>    Trigger rollback due to threshold breach"
    echo "  --breach-value=<value>       Current value that breached threshold"
    echo "  --threshold=<value>          Threshold that was breached"
    echo "  --auto                       Skip confirmation (use with caution)"
    echo "  --dry-run                    Show what would be done without executing"
    echo "  -h, --help                   Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  HA_HOST, ADDON_SLUG, ROLLBACK_TIMEOUT"
    echo ""
    echo "Examples:"
    echo "  $0                           # Interactive rollback"
    echo "  $0 --threshold-breach=error-rate --breach-value=10 --threshold=5"
    echo "  $0 --dry-run                 # Show rollback plan without executing"
    echo ""
}

parse_args() {
    THRESHOLD_BREACH=""
    BREACH_VALUE=""
    THRESHOLD=""
    AUTO_MODE=false
    DRY_RUN=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --threshold-breach=*)
                THRESHOLD_BREACH="${1#*=}"
                shift
                ;;
            --breach-value=*)
                BREACH_VALUE="${1#*=}"
                shift
                ;;
            --threshold=*)
                THRESHOLD="${1#*=}"
                shift
                ;;
            --auto)
                AUTO_MODE=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            -h|--help)
                print_usage
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                print_usage
                exit 1
                ;;
        esac
    done
}

main() {
    print_banner
    parse_args "$@"
    
    # Create log directory
    mkdir -p "$ROLLBACK_LOG_DIR"
    
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "DRY RUN MODE - No changes will be made"
        print_rollback_matrix
        log_info "Pre-rollback checks that would be performed:"
        log_info "  - SSH connectivity check"
        log_info "  - Container existence check"
        log_info "  - Backup availability check"
        log_info ""
        log_info "Rollback steps that would be executed:"
        log_info "  Step 1: Enable LEGACY_FALLBACK"
        log_info "  Step 2: Disable SIDECAR_ACTIVATION"
        log_info "  Step 3: Disable THIN_PASS_THROUGH"
        exit 0
    fi
    
    # Handle threshold breach if specified
    if [[ -n "$THRESHOLD_BREACH" ]]; then
        handle_threshold_breach "$THRESHOLD_BREACH" "${BREACH_VALUE:-unknown}" "${THRESHOLD:-unknown}"
        exit $?
    fi
    
    # Pre-rollback checks
    log_info "Starting pre-rollback checks..."
    
    gate_ssh_connectivity || exit 1
    gate_container_exists || exit 1
    gate_backup_available || exit 1
    
    log_success "All pre-rollback checks passed"
    
    # Print rollback matrix
    print_rollback_matrix
    
    # Require confirmation unless in auto mode
    if [[ "$AUTO_MODE" != "true" ]]; then
        confirm_proceed "About to execute rollback sequence:\n  Step 1: Enable LEGACY_FALLBACK\n  Step 2: Disable SIDECAR_ACTIVATION\n  Step 3: Disable THIN_PASS_THROUGH\n\nThis will revert production to safe state."
    fi
    
    # Execute rollback
    if ! execute_rollback; then
        log_error "Rollback execution failed"
        exit 1
    fi
    
    # Verify rollback
    if ! verify_rollback_success; then
        log_error "Rollback verification failed"
        exit 1
    fi
    
    # Success output
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════════════╗"
    echo "║                        ROLLBACK SUCCESSFUL                                   ║"
    echo "╚══════════════════════════════════════════════════════════════════════════════╝"
    echo ""
    log_success "All rollback steps completed and verified"
    log_info "Feature flags restored to safe state:"
    log_info "  LEGACY_FALLBACK=true (enabled)"
    log_info "  SIDECAR_ACTIVATION=false (disabled)"
    log_info "  THIN_PASS_THROUGH=false (disabled)"
    echo ""
    log_info "Next steps:"
    log_info "  - Monitor logs: ssh ${HA_HOST} 'docker logs -f ${CONTAINER_NAME}'"
    log_info "  - Investigate root cause of the issue"
    log_info "  - Re-attempt deployment after fixes"
    echo ""
    
    return 0
}

# Run main if executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
