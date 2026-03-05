# Enable/Disable Procedure and Rollback Command Path

## Overview
This document provides detailed procedures for enabling/disabling feature flags and the rollback command path for emergency scenarios. All procedures must be executable with clear success/failure outcomes.

## Feature Flag Management

### 1. Enable Procedure: New Offline Logic

#### Prerequisites
- System is in healthy state (verify with `systemctl status oig-proxy`)
- No active processing (verify queue is empty)
- Backup system is operational

#### Step-by-Step Enable

**Step 1: Enable New Offline Logic**
```bash
#!/bin/bash
# Enable new offline logic feature flag
echo "$(date): Enabling FEATURE_NEW_OFFLINE_LOGIC_ENABLED"

# Method 1: Environment variable (requires restart)
if [ -f "/etc/oig-proxy/config.json" ]; then
    # Update config file
    jq '.features.new_offline_logic_enabled = true' /etc/oig-proxy/config.json > /tmp/config.json
    mv /tmp/config.json /etc/oig-proxy/config.json
    
    # Restart service
    systemctl restart oig-proxy
    
    # Verify status
    if systemctl is-active --quiet oig-proxy; then
        echo "✅ FEATURE_NEW_OFFLINE_LOGIC_ENABLED enabled successfully"
        exit 0
    else
        echo "❌ Service failed to restart after enabling new offline logic"
        exit 1
    fi
fi

# Method 2: Runtime API (if available)
if curl -f http://localhost:8080/api/health; then
    response=$(curl -s -X POST http://localhost:8080/api/feature_flags \
      -H "Content-Type: application/json" \
      -d '{"FEATURE_NEW_OFFLINE_LOGIC_ENABLED": true}')
    
    if echo "$response" | grep -q '"status":"success"'; then
        echo "✅ FEATURE_NEW_OFFLINE_LOGIC_ENABLED enabled via API"
        exit 0
    else
        echo "❌ API failed to enable feature flag: $response"
        exit 1
    fi
fi

echo "❌ No enable method available"
exit 1
```

**Step 2: Verify Enablement**
```bash
#!/bin/bash
# Verify feature flag is enabled
echo "$(date): Verifying FEATURE_NEW_OFFLINE_LOGIC_ENABLED status"

# Check service logs
if journalctl -u oig-proxy --since '5 minutes ago' | grep -q "FEATURE.*NEW_OFFLINE_LOGIC_ENABLED.*true"; then
    echo "✅ Feature flag confirmed enabled in logs"
else
    echo "⚠️  Feature flag not confirmed in logs, checking API..."
fi

# Check via API
if curl -f http://localhost:8080/api/health; then
    status=$(curl -s http://localhost:8080/api/feature_flags | jq -r '.FEATURE_NEW_OFFLINE_LOGIC_ENABLED')
    if [ "$status" = "true" ]; then
        echo "✅ Feature flag confirmed enabled via API"
        exit 0
    else
        echo "❌ Feature flag not enabled (API reports: $status)"
        exit 1
    fi
fi

echo "❌ Cannot verify feature flag status"
exit 1
```

### 2. Disable Procedure: New Offline Logic

#### Emergency Disable
```bash
#!/bin/bash
# Emergency disable of new offline logic
echo "$(date): EMERGENCY DISABLE - FEATURE_NEW_OFFLINE_LOGIC_ENABLED"

# Immediate disable via config (safe method)
if [ -f "/etc/oig-proxy/config.json" ]; then
    # Force disable in config
    jq '.features.new_offline_logic_enabled = false' /etc/oig-proxy/config.json > /tmp/config.json
    mv /tmp/config.json /etc/oig-proxy/config.json
    
    # Force restart service
    systemctl restart oig-proxy
    
    # Wait for service to be ready
    for i in {1..30}; do
        if systemctl is-active --quiet oig-proxy; then
            echo "✅ Service restarted successfully"
            break
        fi
        sleep 1
    done
    
    # Verify disablement
    if journalctl -u oig-proxy --since '30 seconds ago' | grep -q "FEATURE.*NEW_OFFLINE_LOGIC_ENABLED.*false"; then
        echo "✅ Emergency disable successful"
        exit 0
    else
        echo "❌ Emergency disable verification failed"
        exit 1
    fi
fi

echo "❌ Emergency disable failed - manual intervention required"
exit 1
```

## Complete Rollback Command Path

### Full Rollback Sequence
```bash
#!/bin/bash
# Complete rollback to legacy logic
echo "$(date): INITIATING COMPLETE ROLLBACK"

# Step 1: Disable all new feature flags
echo "Step 1: Disabling all new feature flags"

FLAGS=(
    "FEATURE_NEW_OFFLINE_LOGIC_ENABLED"
    "FEATURE_NEW_MOCK_LOGIC_ENABLED"
    "FEATURE_HYBRID_AUTO_FAILOVER_ENABLED"
    "FEATURE_NEW_RETRY_LOGIC_ENABLED"
)

for flag in "${FLAGS[@]}"; do
    echo "Disabling $flag"
    curl -s -X POST http://localhost:8080/api/feature_flags \
      -H "Content-Type: application/json" \
      -d "{\"$flag\": false}" > /tmp/rollback_$flag.log
    
    if [ $? -eq 0 ]; then
        echo "✅ $flag disabled"
    else
        echo "❌ Failed to disable $flag"
        cat /tmp/rollback_$flag.log
    fi
done

# Step 2: Graceful drain period
echo "Step 2: Starting 300 second drain period"
sleep 300

# Step 3: Verify rollback
echo "Step 3: Verifying rollback"

if curl -f http://localhost:8080/api/health; then
    status=$(curl -s http://localhost:8080/api/feature_flags)
    offline_logic=$(echo "$status" | jq -r '.FEATURE_NEW_OFFLINE_LOGIC_ENABLED')
    mock_logic=$(echo "$status" | jq -r '.FEATURE_NEW_MOCK_LOGIC_ENABLED')
    
    if [ "$offline_logic" = "false" ] && [ "$mock_logic" = "false" ]; then
        echo "✅ Rollback verified - all new logic disabled"
    else
        echo "❌ Rollback verification failed"
        echo "Offline logic: $offline_logic"
        echo "Mock logic: $mock_logic"
        exit 1
    fi
fi

# Step 4: System health check
echo "Step 4: Performing system health check"

if systemctl is-active --quiet oig-proxy; then
    echo "✅ Service is running"
else
    echo "❌ Service is not running"
    exit 1
fi

# Check for critical errors
if journalctl -u oig-proxy --since '5 minutes ago' | grep -q 'CRITICAL'; then
    echo "⚠️  Critical errors found in logs - investigate"
else
    echo "✅ No critical errors in recent logs"
fi

echo "🎉 ROLLBACK COMPLETED SUCCESSFULLY"
exit 0
```

### Emergency Rollback (Single Command)
```bash
#!/bin/bash
# Emergency rollback - use when system is unstable
echo "$(date): EMERGENCY ROLLBACK INITIATED"

# Force disable all flags and restart
cat > /tmp/emergency_rollback.json << EOF
{
  "features": {
    "new_offline_logic_enabled": false,
    "new_mock_logic_enabled": false,
    "hybrid_auto_failover_enabled": true,
    "new_retry_logic_enabled": false
  }
}
EOF

# Update config and restart
if [ -f "/etc/oig-proxy/config.json" ]; then
    cp /etc/oig-proxy/config.json /etc/oig-proxy/config.json.backup.$(date +%s)
    jq '.features = {
      "new_offline_logic_enabled": false,
      "new_mock_logic_enabled": false, 
      "hybrid_auto_failover_enabled": true,
      "new_retry_logic_enabled": false
    }' /etc/oig-proxy/config.json > /tmp/config.json
    mv /tmp/config.json /etc/oig-proxy/config.json
    
    # Force restart
    systemctl restart oig-proxy
    
    # Wait for service
    sleep 10
    
    if systemctl is-active --quiet oig-proxy; then
        echo "✅ Emergency rollback completed"
        echo "Backup config saved as: /etc/oig-proxy/config.json.backup.$(date +%s)"
        exit 0
    else
        echo "❌ Emergency rollback failed - service not running"
        echo "Manual intervention required"
        exit 1
    fi
else
    echo "❌ Config file not found - cannot perform emergency rollback"
    exit 1
fi
```

## Operational Procedures

### Health Check Before Enable
```bash
#!/bin/bash
# Pre-enable health check
echo "$(date): Performing pre-enable health check"

# Check service status
if ! systemctl is-active --quiet oig-proxy; then
    echo "❌ Service is not running"
    exit 1
fi

# Check disk space
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 80 ]; then
    echo "⚠️  High disk usage: ${DISK_USAGE}%"
fi

# Check memory
MEMORY_USAGE=$(free | grep Mem | awk '{printf "%.0f", $3/$2 * 100}')
if [ "$MEMORY_USAGE" -gt 80 ]; then
    echo "⚠️  High memory usage: ${MEMORY_USAGE}%"
fi

# Check queue is empty (if applicable)
if [ -f "/data/cloud_queue.db" ]; then
    QUEUE_SIZE=$(sqlite3 /data/cloud_queue.db "SELECT COUNT(*) FROM frames")
    if [ "$QUEUE_SIZE" -gt 0 ]; then
        echo "⚠️  Queue not empty: $QUEUE_SIZE frames pending"
    else
        echo "✅ Queue is empty"
    fi
fi

echo "✅ Pre-enable health check passed"
exit 0
```

### Post-Enable Verification
```bash
#!/bin/bash
# Post-enable verification
echo "$(date): Performing post-enable verification"

# Wait 60 seconds for stabilization
sleep 60

# Check feature flag status
if curl -f http://localhost:8080/api/feature_flags; then
    echo "✅ Feature flags API accessible"
else
    echo "❌ Feature flags API not accessible"
    exit 1
fi

# Check for error rates
ERROR_RATE=$(curl -s http://localhost:8080/metrics | grep 'error_rate' | awk '{print $2}' | head -1)
if [ -n "$ERROR_RATE" ] && awk "BEGIN {exit !($ERROR_RATE < 0.05)}"; then
    echo "✅ Error rate acceptable: $ERROR_RATE"
else
    echo "⚠️  High error rate: $ERROR_RATE"
fi

# Check service is still running
if systemctl is-active --quiet oig-proxy; then
    echo "✅ Service is still running"
else
    echo "❌ Service stopped after enable"
    exit 1
fi

echo "✅ Post-enable verification passed"
exit 0
```

## Monitoring and Alerting

### Rollback Status Monitoring
```bash
#!/bin/bash
# Monitor rollback status
echo "$(date): Checking rollback status"

# Check if rollback is in progress
if pgrep -f "rollback" > /dev/null; then
    echo "⚠️  Rollback process running"
    
    # Check how long it's been running
    ROLLBACK_PID=$(pgrep -f "rollback")
    ROLLBACK_START=$(ps -p $ROLLBACK_PID -o lstart= | xargs -I{} date -d "{}" +%s)
    CURRENT_TIME=$(date +%s)
    ROLLBACK_DURATION=$((CURRENT_TIME - ROLLBACK_START))
    
    if [ "$ROLLBACK_DURATION" -gt 300 ]; then
        echo "❌ Rollback taking too long (${ROLLBACK_DURATION}s)"
        # Send alert
        curl -X POST "https://alerts.example.com" \
          -d "message=ROLLBACK_STUCK_DURATION_${ROLLBACK_DURATION}"
    fi
fi

# Check if rollback was recently completed
if journalctl -u oig-proxy --since '1 hour ago' | grep -q "ROLLBACK COMPLETED"; then
    echo "✅ Recent rollback completed successfully"
fi

# Check if there are rollback-related errors
if journalctl -u oig-proxy --since '1 hour ago' | grep -q "rollback.*fail\|fail.*rollback"; then
    echo "❌ Rollback-related errors detected"
    # Send alert
    curl -X POST "https://alerts.example.com" \
      -d "message=ROLLBACK_ERROR_DETECTED"
fi

exit 0
```

---

*This document provides comprehensive operational procedures for feature flag management and rollback scenarios.*