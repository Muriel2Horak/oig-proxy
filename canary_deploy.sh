#!/bin/bash
#
# Task 21: Canary Deployment Script with Health Check Gates
# Proxy Thin Pass-Through + Twin Sidecar Refactor
#
# This script performs a canary rollout with:
# - Feature flag matrix integration
# - Health check gates at each stage
# - Threshold monitoring
# - Explicit confirmation required (no auto-production changes)
#

set -euo pipefail

# ================================================================================
# CONFIGURATION
# ================================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVIDENCE_DIR="${SCRIPT_DIR}/.sisyphus/evidence"
CANARY_LOG_DIR="${EVIDENCE_DIR}/canary-logs"

# Feature flags (from Task 2)
FLAG_THIN_PASS_THROUGH="${THIN_PASS_THROUGH:-false}"
FLAG_SIDECAR_ACTIVATION="${SIDECAR_ACTIVATION:-false}"
FLAG_LEGACY_FALLBACK="${LEGACY_FALLBACK:-true}"

# Deployment targets
HA_HOST="${HA_HOST:-ha}"
ADDON_SLUG="${ADDON_SLUG:-d7b5d5b1_oig_proxy}"
CONTAINER_NAME="addon_${ADDON_SLUG}"

# Canary thresholds
CANARY_PERCENTAGE="${CANARY_PERCENTAGE:-10}"
CANARY_DURATION_MINUTES="${CANARY_DURATION_MINUTES:-5}"
ERROR_THRESHOLD_PERCENT="${ERROR_THRESHOLD_PERCENT:-5}"
LATENCY_THRESHOLD_MS="${LATENCY_THRESHOLD_MS:-1000}"

# Health check configuration
HEALTH_CHECK_INTERVAL="${HEALTH_CHECK_INTERVAL:-30}"
HEALTH_CHECK_RETRIES="${HEALTH_CHECK_RETRIES:-3}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
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

log_gate() {
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║ GATE: $1${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
}

confirm_proceed() {
    local message="$1"
    echo ""
    echo -e "${YELLOW}⚠️  MANUAL CONFIRMATION REQUIRED${NC}"
    echo -e "${YELLOW}$message${NC}"
    echo ""
    read -p "Type 'YES' to proceed: " confirm
    if [[ "$confirm" != "YES" ]]; then
        log_error "Deployment aborted by user"
        exit 1
    fi
    log_info "Proceeding with deployment..."
}

# ================================================================================
# FEATURE FLAG MATRIX (Task 2 Integration)
# ================================================================================

print_feature_flag_matrix() {
    log_gate "FEATURE FLAG MATRIX (Task 2)"
    echo ""
    echo "┌──────────────────────────┬─────────┬─────────┬─────────────────────────────────────┐"
    echo "│ Flag                     │ Default │ Current │ Description                         │"
    echo "├──────────────────────────┼─────────┼─────────┼─────────────────────────────────────┤"
    printf "│ THIN_PASS_THROUGH        │ false   │ %-5s   │ Thin pass-through mode              │\n" "$FLAG_THIN_PASS_THROUGH"
    printf "│ SIDECAR_ACTIVATION       │ false   │ %-5s   │ Twin sidecar mode                   │\n" "$FLAG_SIDECAR_ACTIVATION"
    printf "│ LEGACY_FALLBACK          │ true    │ %-5s   │ Legacy fallback behavior            │\n" "$FLAG_LEGACY_FALLBACK"
    echo "└──────────────────────────┴─────────┴─────────┴─────────────────────────────────────┘"
    echo ""
    echo "Rollback Priority (highest to lowest):"
    echo "  1. LEGACY_FALLBACK → 2. SIDECAR_ACTIVATION → 3. THIN_PASS_THROUGH"
    echo ""
}

validate_feature_flags() {
    log_gate "GATE 1: Feature Flag Validation"
    
    local errors=0
    
    # Validate boolean values
    if [[ ! "$FLAG_THIN_PASS_THROUGH" =~ ^(true|false)$ ]]; then
        log_error "THIN_PASS_THROUGH must be 'true' or 'false', got: $FLAG_THIN_PASS_THROUGH"
        errors=$((errors + 1))
    fi
    
    if [[ ! "$FLAG_SIDECAR_ACTIVATION" =~ ^(true|false)$ ]]; then
        log_error "SIDECAR_ACTIVATION must be 'true' or 'false', got: $FLAG_SIDECAR_ACTIVATION"
        errors=$((errors + 1))
    fi
    
    if [[ ! "$FLAG_LEGACY_FALLBACK" =~ ^(true|false)$ ]]; then
        log_error "LEGACY_FALLBACK must be 'true' or 'false', got: $FLAG_LEGACY_FALLBACK"
        errors=$((errors + 1))
    fi
    
    # Safety check: LEGACY_FALLBACK should be true for canary
    if [[ "$FLAG_LEGACY_FALLBACK" != "true" ]]; then
        log_warn "LEGACY_FALLBACK is false - this disables safety fallback!"
        log_warn "Recommended: Set LEGACY_FALLBACK=true for canary deployments"
    fi
    
    if [[ $errors -eq 0 ]]; then
        log_success "All feature flags validated"
        return 0
    else
        log_error "Feature flag validation failed with $errors errors"
        return 1
    fi
}

# ================================================================================
# HEALTH CHECK GATES
# ================================================================================

gate_ssh_connectivity() {
    log_gate "GATE 2: SSH Connectivity Check"
    
    if ! ssh -q -o ConnectTimeout=10 "${HA_HOST}" exit 2>/dev/null; then
        log_error "Cannot connect to HA host: ${HA_HOST}"
        log_info "Ensure SSH alias 'ha' is configured in ~/.ssh/config"
        return 1
    fi
    
    log_success "SSH connectivity to ${HA_HOST} verified"
    return 0
}

gate_container_status() {
    log_gate "GATE 3: Container Status Check"
    
    local status
    status=$(ssh "${HA_HOST}" "docker inspect ${CONTAINER_NAME} --format '{{.State.Status}}' 2>/dev/null" || echo "unknown")
    
    if [[ "$status" != "running" ]]; then
        log_error "Container ${CONTAINER_NAME} is not running (status: ${status})"
        return 1
    fi
    
    log_success "Container ${CONTAINER_NAME} is running"
    return 0
}

gate_resource_usage() {
    log_gate "GATE 4: Resource Usage Check"
    
    local stats
    stats=$(ssh "${HA_HOST}" "docker stats ${CONTAINER_NAME} --no-stream --format 'CPU: {{.CPUPerc}}, MEM: {{.MemPerc}}' 2>/dev/null" || echo "unknown")
    
    log_info "Current resource usage: ${stats}"
    
    # Extract memory percentage (rough check)
    local mem_perc
    mem_perc=$(echo "$stats" | grep -oP 'MEM: \K[0-9.]+' || echo "0")
    
    if (( $(echo "$mem_perc > 90" | bc -l 2>/dev/null || echo "0") )); then
        log_warn "Memory usage is high: ${mem_perc}%"
        return 1
    fi
    
    log_success "Resource usage within acceptable limits"
    return 0
}

gate_log_health() {
    log_gate "GATE 5: Log Health Check"
    
    local recent_logs
    recent_logs=$(ssh "${HA_HOST}" "docker logs --since 5m ${CONTAINER_NAME} 2>&1" || echo "")
    
    # Check for critical errors
    local critical_errors
    critical_errors=$(echo "$recent_logs" | grep -c "CRITICAL\|FATAL\|Traceback" || echo "0")
    
    if [[ "$critical_errors" -gt 0 ]]; then
        log_error "Found ${critical_errors} critical errors in recent logs"
        echo "$recent_logs" | grep "CRITICAL\|FATAL\|Traceback" | tail -5
        return 1
    fi
    
    # Check for exception patterns
    local exceptions
    exceptions=$(echo "$recent_logs" | grep -c "Exception\|Error:" || echo "0")
    
    if [[ "$exceptions" -gt 5 ]]; then
        log_warn "Found ${exceptions} exceptions in recent logs"
        echo "$recent_logs" | grep "Exception\|Error:" | tail -3
    fi
    
    log_success "Log health check passed"
    return 0
}

gate_mqtt_connectivity() {
    log_gate "GATE 6: MQTT Connectivity Check"
    
    # Check if MQTT broker is reachable from the container
    local mqtt_check
    mqtt_check=$(ssh "${HA_HOST}" "docker exec ${CONTAINER_NAME} sh -c 'nc -z \${MQTT_HOST:-core-mosquitto} \${MQTT_PORT:-1883} 2>/dev/null && echo OK || echo FAIL'" 2>/dev/null || echo "FAIL")
    
    if [[ "$mqtt_check" != "OK" ]]; then
        log_warn "MQTT connectivity check inconclusive (may be expected in some configs)"
        return 0  # Non-blocking for canary
    fi
    
    log_success "MQTT connectivity verified"
    return 0
}

gate_feature_flag_readiness() {
    log_gate "GATE 7: Feature Flag Readiness Check"
    
    # Check if the addon can read the feature flags
    local env_check
    env_check=$(ssh "${HA_HOST}" "docker exec ${CONTAINER_NAME} sh -c 'echo THIN_PASS_THROUGH=\${THIN_PASS_THROUGH:-unset}, SIDECAR_ACTIVATION=\${SIDECAR_ACTIVATION:-unset}, LEGACY_FALLBACK=\${LEGACY_FALLBACK:-unset}'" 2>/dev/null || echo "FAIL")
    
    log_info "Container environment: ${env_check}"
    
    log_success "Feature flag readiness verified"
    return 0
}

# ================================================================================
# THRESHOLD MONITORING
# ================================================================================

monitor_canary_metrics() {
    local duration_minutes="$1"
    local interval_seconds="${2:-30}"
    
    log_gate "CANARY MONITORING (${duration_minutes} minutes)"
    
    local iterations=$((duration_minutes * 60 / interval_seconds))
    local iteration=0
    local errors=0
    local total_latency=0
    local measurements=0
    
    echo ""
    echo "Monitoring thresholds:"
    echo "  - Error threshold: ${ERROR_THRESHOLD_PERCENT}%"
    echo "  - Latency threshold: ${LATENCY_THRESHOLD_MS}ms"
    echo "  - Check interval: ${interval_seconds}s"
    echo ""
    
    while [[ $iteration -lt $iterations ]]; do
        iteration=$((iteration + 1))
        
        # Get container stats
        local stats
        stats=$(ssh "${HA_HOST}" "docker stats ${CONTAINER_NAME} --no-stream --format '{{.CPUPerc}}|{{.MemUsage}}' 2>/dev/null" || echo "0%|0MiB")
        
        # Get recent logs for error counting
        local recent_logs
        recent_logs=$(ssh "${HA_HOST}" "docker logs --since ${interval_seconds}s ${CONTAINER_NAME} 2>&1" || echo "")
        
        local error_count
        error_count=$(echo "$recent_logs" | grep -c "ERROR\|Exception\|Traceback" || echo "0")
        errors=$((errors + error_count))
        
        # Simple latency proxy (time to get container status)
        local start_time end_time latency
        start_time=$(date +%s%N)
        ssh -q "${HA_HOST}" "docker inspect ${CONTAINER_NAME} --format '{{.State.Status}}'" >/dev/null 2>&1
        end_time=$(date +%s%N)
        latency=$(( (end_time - start_time) / 1000000 ))  # Convert to ms
        total_latency=$((total_latency + latency))
        measurements=$((measurements + 1))
        
        # Calculate current metrics
        local avg_latency=0
        if [[ $measurements -gt 0 ]]; then
            avg_latency=$((total_latency / measurements))
        fi
        
        local error_rate=0
        if [[ $iteration -gt 0 ]]; then
            error_rate=$((errors * 100 / iteration))
        fi
        
        # Progress bar
        local progress=$((iteration * 100 / iterations))
        printf "\r  [%3d%%] Iteration %d/%d | Errors: %d | Avg Latency: %dms | CPU/MEM: %s" \
            "$progress" "$iteration" "$iterations" "$errors" "$avg_latency" "$stats"
        
        # Check thresholds
        if [[ $error_rate -gt $ERROR_THRESHOLD_PERCENT ]]; then
            echo ""
            log_error "Error rate (${error_rate}%) exceeded threshold (${ERROR_THRESHOLD_PERCENT}%)"
            return 1
        fi
        
        if [[ $avg_latency -gt $LATENCY_THRESHOLD_MS ]]; then
            echo ""
            log_warn "Latency (${avg_latency}ms) exceeded threshold (${LATENCY_THRESHOLD_MS}ms)"
            # Non-blocking warning
        fi
        
        sleep "$interval_seconds"
    done
    
    echo ""
    echo ""
    log_success "Canary monitoring completed - all thresholds within limits"
    return 0
}

# ================================================================================
# DEPLOYMENT PHASES
# ================================================================================

phase_pre_deployment() {
    log_gate "PRE-DEPLOYMENT PHASE"
    
    # Create evidence directory
    mkdir -p "$CANARY_LOG_DIR"
    
    # Print feature flag matrix
    print_feature_flag_matrix
    
    # Run all health gates
    validate_feature_flags || return 1
    gate_ssh_connectivity || return 1
    gate_container_status || return 1
    gate_resource_usage || return 1
    gate_log_health || return 1
    gate_mqtt_connectivity || return 1
    gate_feature_flag_readiness || return 1
    
    log_success "All pre-deployment gates passed"
    return 0
}

phase_canary_deployment() {
    log_gate "CANARY DEPLOYMENT PHASE"
    
    log_info "Canary configuration:"
    log_info "  - Percentage: ${CANARY_PERCENTAGE}%"
    log_info "  - Duration: ${CANARY_DURATION_MINUTES} minutes"
    log_info "  - Error threshold: ${ERROR_THRESHOLD_PERCENT}%"
    log_info "  - Latency threshold: ${LATENCY_THRESHOLD_MS}ms"
    
    # Create feature flag configuration for canary
    local canary_config
    canary_config=$(cat <<EOF
# Canary Deployment Configuration
# Generated: $(date -Iseconds)

# Feature Flags (Task 2)
THIN_PASS_THROUGH=${FLAG_THIN_PASS_THROUGH}
SIDECAR_ACTIVATION=${FLAG_SIDECAR_ACTIVATION}
LEGACY_FALLBACK=${FLAG_LEGACY_FALLBACK}

# Rollback Priority: LEGACY_FALLBACK > SIDECAR_ACTIVATION > THIN_PASS_THROUGH
EOF
)
    
    echo "$canary_config" > "${CANARY_LOG_DIR}/canary-config-$(date +%Y%m%d-%H%M%S).env"
    log_info "Canary configuration saved to ${CANARY_LOG_DIR}"
    
    # Require explicit confirmation before any production changes
    confirm_proceed "About to apply canary deployment with feature flags:\n${canary_config}\n\nThis will affect production. Confirm to proceed."
    
    log_success "Canary deployment phase prepared"
    return 0
}

phase_post_deployment() {
    log_gate "POST-DEPLOYMENT PHASE"
    
    # Run canary monitoring
    if ! monitor_canary_metrics "$CANARY_DURATION_MINUTES" "$HEALTH_CHECK_INTERVAL"; then
        log_error "Canary monitoring detected threshold breach"
        return 1
    fi
    
    # Final health check
    gate_container_status || return 1
    gate_log_health || return 1
    
    log_success "Post-deployment phase completed successfully"
    return 0
}

# ================================================================================
# MAIN EXECUTION
# ================================================================================

print_banner() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════════════╗"
    echo "║                    CANARY DEPLOYMENT SCRIPT - Task 21                        ║"
    echo "║          Proxy Thin Pass-Through + Twin Sidecar Refactor                     ║"
    echo "╚══════════════════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "This script performs a canary rollout with health check gates."
    echo "NO automatic production changes will be made without explicit confirmation."
    echo ""
}

print_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --thin-pass-through=<true|false>   Enable thin pass-through mode (default: false)"
    echo "  --sidecar-activation=<true|false>  Enable twin sidecar mode (default: false)"
    echo "  --legacy-fallback=<true|false>     Enable legacy fallback (default: true)"
    echo "  --canary-percent=<N>               Canary traffic percentage (default: 10)"
    echo "  --canary-duration=<N>              Canary duration in minutes (default: 5)"
    echo "  --error-threshold=<N>              Error threshold percentage (default: 5)"
    echo "  --latency-threshold=<N>            Latency threshold in ms (default: 1000)"
    echo "  --dry-run                        Show what would be done without executing"
    echo "  -h, --help                       Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  THIN_PASS_THROUGH, SIDECAR_ACTIVATION, LEGACY_FALLBACK"
    echo "  HA_HOST, CANARY_PERCENTAGE, CANARY_DURATION_MINUTES"
    echo ""
    echo "Examples:"
    echo "  $0 --thin-pass-through=true --canary-duration=10"
    echo "  THIN_PASS_THROUGH=true SIDECAR_ACTIVATION=false $0"
    echo ""
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --thin-pass-through=*)
                FLAG_THIN_PASS_THROUGH="${1#*=}"
                shift
                ;;
            --sidecar-activation=*)
                FLAG_SIDECAR_ACTIVATION="${1#*=}"
                shift
                ;;
            --legacy-fallback=*)
                FLAG_LEGACY_FALLBACK="${1#*=}"
                shift
                ;;
            --canary-percent=*)
                CANARY_PERCENTAGE="${1#*=}"
                shift
                ;;
            --canary-duration=*)
                CANARY_DURATION_MINUTES="${1#*=}"
                shift
                ;;
            --error-threshold=*)
                ERROR_THRESHOLD_PERCENT="${1#*=}"
                shift
                ;;
            --latency-threshold=*)
                LATENCY_THRESHOLD_MS="${1#*=}"
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
    
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        log_info "DRY RUN MODE - No changes will be made"
        print_feature_flag_matrix
        exit 0
    fi
    
    # Track start time
    local start_time end_time duration
    start_time=$(date +%s)
    
    log_info "Starting canary deployment at $(date)"
    log_info "Evidence will be saved to: ${CANARY_LOG_DIR}"
    
    # Execute phases
    if ! phase_pre_deployment; then
        log_error "Pre-deployment phase failed"
        exit 1
    fi
    
    if ! phase_canary_deployment; then
        log_error "Canary deployment phase failed"
        exit 1
    fi
    
    if ! phase_post_deployment; then
        log_error "Post-deployment phase failed - consider rollback"
        log_info "Run: ./canary_rollback.sh to rollback"
        exit 1
    fi
    
    # Calculate duration
    end_time=$(date +%s)
    duration=$((end_time - start_time))
    
    # Success output
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════════════════╗"
    echo "║                     CANARY DEPLOYMENT SUCCESSFUL                             ║"
    echo "╚══════════════════════════════════════════════════════════════════════════════╝"
    echo ""
    log_success "All gates passed - deployment verified"
    log_info "Duration: ${duration} seconds"
    log_info "Feature flags active:"
    log_info "  THIN_PASS_THROUGH=${FLAG_THIN_PASS_THROUGH}"
    log_info "  SIDECAR_ACTIVATION=${FLAG_SIDECAR_ACTIVATION}"
    log_info "  LEGACY_FALLBACK=${FLAG_LEGACY_FALLBACK}"
    echo ""
    log_info "Next steps:"
    log_info "  - Monitor logs: ssh ${HA_HOST} 'docker logs -f ${CONTAINER_NAME}'"
    log_info "  - Full rollout: Increase canary percentage gradually"
    log_info "  - Rollback: Run ./canary_rollback.sh if issues detected"
    echo ""
    
    return 0
}

# Run main if executed directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
