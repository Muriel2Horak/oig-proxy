# Rollback Gate Specification: Backup Removal Criteria

## Overview
This document defines the hard conditions for backup removal. All criteria must pass as binary (pass/fail) checks before any legacy code or backup systems can be safely removed.

## Gate Principles
- **Binary**: Each criterion is strictly pass/fail
- **Auditable**: Results must be automatically verifiable
- **Safe**: Failed gates block removal and point to rollback procedures
- **Comprehensive**: Covers functionality, performance, reliability, and operations

## Binary Checklist for Backup Removal

### CRITICAL GATES (Must ALL pass)

#### GATE 1: Feature Flag Stability
**Check**: Feature flags have been stable for required period
```bash
#!/bin/bash
# Check: All feature flags enabled for minimum 30 days
FLAG_STABLE_DAYS=$(sqlite3 /data/payloads.db \
  "SELECT MIN(julianday('now') - julianday(timestamp)) 
   FROM feature_flag_changes 
   WHERE flag_name IN ('FEATURE_NEW_OFFLINE_LOGIC_ENABLED', 'FEATURE_NEW_MOCK_LOGIC_ENABLED')
   AND new_value = 'true'")

if [ "$FLAG_STABLE_DAYS" -ge 30 ]; then
    echo "PASS: Feature flags stable for $FLAG_STABLE_DAYS days"
    exit 0
else
    echo "FAIL: Feature flags not stable for 30 days (current: $FLAG_STABLE_DAYS)"
    exit 1
fi
```

#### GATE 2: Error Rate Comparison
**Check**: New logic error rate ≤ legacy logic error rate
```bash
#!/bin/bash
# Check: Error rate comparison from metrics
LEGACY_ERROR_RATE=$(curl -s http://localhost:8080/metrics | grep 'legacy_error_rate' | awk '{print $2}')
NEW_ERROR_RATE=$(curl -s http://localhost:8080/metrics | grep 'new_error_rate' | awk '{print $2}')

# Compare using awk for floating point comparison
if awk "BEGIN {exit !($NEW_ERROR_RATE <= $LEGACY_ERROR_RATE)}"; then
    echo "PASS: New error rate ($NEW_ERROR_RATE) ≤ legacy ($LEGACY_ERROR_RATE)"
    exit 0
else
    echo "FAIL: New error rate ($NEW_ERROR_RATE) > legacy ($LEGACY_ERROR_RATE)"
    exit 1
fi
```

#### GATE 3: Performance Requirements
**Check**: New logic performance ≥ legacy logic performance
```bash
#!/bin/bash
# Check: Latency comparison (95th percentile)
LEGACY_P95_LATENCY=$(curl -s http://localhost:8080/metrics | grep 'legacy_latency_p95' | awk '{print $2}')
NEW_P95_LATENCY=$(curl -s http://localhost:8080/metrics | grep 'new_latency_p95' | awk '{print $2}')

if awk "BEGIN {exit !($NEW_P95_LATENCY <= $LEGACY_P95_LATENCY * 1.1)}"; then
    echo "PASS: New latency ($NEW_P95_LATENCY) ≤ legacy + 10% ($LEGACY_P95_LATENCY)"
    exit 0
else
    echo "FAIL: New latency ($NEW_P95_LATENCY) > legacy + 10% ($LEGACY_P95_LATENCY)"
    exit 1
fi
```

#### GATE 4: Functional Validation
**Check**: All critical functionality tests pass
```bash
#!/bin/bash
# Check: Functional test results
TEST_RESULTS_FILE="/tmp/functional_test_results.json"

# Run functional tests
python -m pytest tests/functional/test_offline_logic.py -v --json-report --json-report-file=$TEST_RESULTS_FILE

# Check if all tests passed
if python -c "
import json
import sys
with open('$TEST_RESULTS_FILE') as f:
    data = json.load(f)
    if data['summary']['failed'] == 0 and data['summary']['passed'] > 0:
        print('PASS: All functional tests passed')
        sys.exit(0)
    else:
        print(f'FAIL: {data[\"summary\"][\"failed\"]} tests failed')
        sys.exit(1)
"; then
    exit 0
else
    exit 1
fi
```

### OPERATIONAL GATES (Must ALL pass)

#### GATE 5: Log Analysis
**Check**: No critical errors in new logic logs
```bash
#!/bin/bash
# Check: Critical errors in logs for last 7 days
CRITICAL_ERRORS=$(journalctl -u oig-proxy --since '7 days ago' | grep -c 'CRITICAL.*new_logic')

if [ "$CRITICAL_ERRORS" -eq 0 ]; then
    echo "PASS: No critical errors in new logic logs"
    exit 0
else
    echo "FAIL: Found $CRITICAL_ERRORS critical errors in new logic logs"
    exit 1
fi
```

#### GATE 6: User Acceptance
**Check**: User validation complete and approved
```bash
#!/bin/bash
# Check: User validation status file
VALIDATION_FILE="/data/user_validation_complete.flag"

if [ -f "$VALIDATION_FILE" ]; then
    VALIDATION_DATE=$(stat -c %Y "$VALIDATION_FILE")
    CURRENT_DATE=$(date +%s)
    DAYS_SINCE_VALIDATION=$(( (CURRENT_DATE - VALIDATION_DATE) / 86400 ))
    
    if [ "$DAYS_SINCE_VALIDATION" -ge 7 ]; then
        echo "PASS: User validation completed $DAYS_SINCE_VALIDATION days ago"
        exit 0
    else
        echo "FAIL: User validation too recent (wait 7 days after validation)"
        exit 1
    fi
else
    echo "FAIL: User validation not completed"
    exit 1
fi
```

#### GATE 7: Backup System Verification
**Check**: Backup systems tested and verified functional
```bash
#!/bin/bash
# Check: Last backup test result
BACKUP_TEST_FILE="/data/backup_test_result.json"

if [ -f "$BACKUP_TEST_FILE" ]; then
    if python -c "
import json
import sys
from datetime import datetime, timedelta

with open('$BACKUP_TEST_FILE') as f:
    data = json.load(f)
    test_date = datetime.fromisoformat(data['test_date'])
    if (datetime.now() - test_date).days <= 1 and data['result'] == 'PASS':
        print('PASS: Backup system tested and verified')
        sys.exit(0)
    else:
        print('FAIL: Backup system test failed or too old')
        sys.exit(1)
"; then
        exit 0
    else
        exit 1
    fi
else
    echo "FAIL: Backup system test not performed"
    exit 1
fi
```

## Gate Execution Procedure

### Automated Gate Check
```bash
#!/bin/bash
# Master gate check script
GATES=(
    "feature_flag_stability"
    "error_rate_comparison" 
    "performance_requirements"
    "functional_validation"
    "log_analysis"
    "user_acceptance"
    "backup_system_verification"
)

PASSED=0
FAILED=0
GATE_RESULTS=()

for gate in "${GATES[@]}"; do
    echo "Checking gate: $gate"
    if /usr/local/bin/gate_checks/check_$gate.sh; then
        ((PASSED++))
        GATE_RESULTS+=("$gate:PASS")
        echo "✅ $gate: PASS"
    else
        ((FAILED++))
        GATE_RESULTS+=("$gate:FAIL")
        echo "❌ $gate: FAIL"
    fi
    echo "---"
done

echo "Gate Summary:"
echo "PASSED: $PASSED"
echo "FAILED: $FAILED"
echo "Results: ${GATE_RESULTS[*]}"

if [ "$FAILED" -eq 0 ]; then
    echo "🎉 ALL GATES PASSED - Backup removal approved"
    exit 0
else
    echo "🚫 GATES FAILED - Backup removal blocked"
    exit 1
fi
```

### Rollback Command Path

#### Immediate Rollback
```bash
#!/bin/bash
# Emergency rollback - disable all new feature flags
curl -X POST http://localhost:8080/api/feature_flags \
  -H "Content-Type: application/json" \
  -d '{
    "FEATURE_NEW_OFFLINE_LOGIC_ENABLED": false,
    "FEATURE_NEW_MOCK_LOGIC_ENABLED": false,
    "FEATURE_HYBRID_AUTO_FAILOVER_ENABLED": true,
    "FEATURE_NEW_RETRY_LOGIC_ENABLED": false
  }'
```

#### Graceful Rollback
```bash
#!/bin/bash
# Graceful rollback - disable new flags with drain period
curl -X POST http://localhost:8080/api/feature_flags \
  -H "Content-Type: application/json" \
  -d '{
    "FEATURE_NEW_MOCK_LOGIC_ENABLED": false,
    "FEATURE_NEW_RETRY_LOGIC_ENABLED": false
  }'

# Wait for existing requests to complete
sleep 300

# Disable remaining new logic
curl -X POST http://localhost:8080/api/feature_flags \
  -H "Content-Type: application/json" \
  -d '{
    "FEATURE_NEW_OFFLINE_LOGIC_ENABLED": false
  }'
```

## Gate Monitoring

### Continuous Monitoring
```bash
#!/bin/bash
# Run gate checks every 6 hours
while true; do
    if /usr/local/bin/gate_checks/run_all_gates.sh; then
        echo "$(date): All gates passed - Ready for backup removal"
    else
        echo "$(date): Gates failed - Check /var/log/gate_checks.log for details"
        # Send alert
        curl -X POST "https://alerts.example.com" \
          -d "message=GATE_CHECK_FAILED"
    fi
    sleep 21600  # 6 hours
done
```

## Documentation and Evidence

### Required Evidence Files
- `.sisyphus/evidence/task-3-gate-dry-run.txt`: Results of dry run execution
- `.sisyphus/evidence/task-3-checklist-failure.txt`: Results of failure scenario test
- `/var/log/gate_checks.log`: Continuous monitoring logs
- `/data/gate_check_results.json`: Historical gate check results

### Audit Trail
All gate executions must be logged with:
- Timestamp
- Gate name and result
- Execution time
- Operator/trigger (manual/automatic)
- Any error messages or failure reasons

---

*This gate specification ensures safe, auditable backup removal with clear rollback procedures.*