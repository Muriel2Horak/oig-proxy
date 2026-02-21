# Feature Flag Specification: Offline+Mock Logic Alignment

## Overview
This document defines the switchable behavior gates for old/new offline+mock logic in the OIG Proxy system. These gates provide controlled migration from legacy to new implementation with clear rollback capabilities.

## Feature Flags

### 1. `FEATURE_NEW_OFFLINE_LOGIC_ENABLED`
**Purpose**: Controls which offline processing logic is used

**Values**:
- `true`: Use new offline logic implementation
- `false`: Use legacy offline logic implementation (default)

**Scope**: 
- Affects frame processing, ACK generation, and queue management in offline mode
- Controls choice between old and new offline handlers
- Impacts `should_try_cloud()` behavior and failure counting

**Dependencies**: None (can be toggled independently)

### 2. `FEATURE_NEW_MOCK_LOGIC_ENABLED`
**Purpose**: Controls which mock/response simulation logic is used

**Values**:
- `true`: Use new mock logic implementation  
- `false`: Use legacy mock logic implementation (default)

**Scope**:
- Affects response generation when in offline/mock scenarios
- Controls simulated device responses and state management
- Impacts telemetry data generation for offline states

**Dependencies**: `FEATURE_NEW_OFFLINE_LOGIC_ENABLED` must be true for this to take effect

### 3. `FEATURE_HYBRID_AUTO_FAILOVER_ENABLED`
**Purpose**: Controls automatic failover behavior in hybrid mode

**Values**:
- `true`: Enable automatic switching to offline on cloud failures (current behavior)
- `false`: Disable automatic failover, require manual intervention (default for new logic)

**Scope**:
- Affects `record_failure()` and `record_success()` behavior
- Controls automatic offline/online transitions
- Impacts fail counting and retry logic

**Dependencies**: None

### 4. `FEATURE_NEW_RETRY_LOGIC_ENABLED`
**Purpose**: Controls retry mechanism behavior

**Values**:
- `true`: Use new retry logic with adaptive intervals
- `false`: Use legacy retry logic with fixed intervals (default)

**Scope**:
- Affects `should_try_cloud()` retry interval calculations
- Controls backoff behavior and retry timing
- Impacts connection attempt frequency

**Dependencies**: None

## Configuration Methods

### Environment Variables (Preferred)
```bash
# Add to addon/oig-proxy/config.json options:
{
  "features": {
    "new_offline_logic_enabled": false,
    "new_mock_logic_enabled": false,
    "hybrid_auto_failover_enabled": true,
    "new_retry_logic_enabled": false
  }
}
```

### Runtime Configuration
```python
# In proxy.py or similar
class FeatureFlags:
    NEW_OFFLINE_LOGIC = os.getenv('FEATURE_NEW_OFFLINE_LOGIC_ENABLED', 'false').lower() == 'true'
    NEW_MOCK_LOGIC = os.getenv('FEATURE_NEW_MOCK_LOGIC_ENABLED', 'false').lower() == 'true'
    HYBRID_AUTO_FAILOVER = os.getenv('FEATURE_HYBRID_AUTO_FAILOVER_ENABLED', 'true').lower() == 'true'
    NEW_RETRY_LOGIC = os.getenv('FEATURE_NEW_RETRY_LOGIC_ENABLED', 'false').lower() == 'true'
```

## Implementation Guidelines

### Gate Logic Pattern
```python
def process_offline_frame(frame):
    if FeatureFlags.NEW_OFFLINE_LOGIC:
        return new_offline_processor(frame)
    else:
        return legacy_offline_processor(frame)
```

### Feature Flag Hierarchy
```
FEATURE_NEW_OFFLINE_LOGIC_ENABLED
├── Controls: Offline frame processing
├── Controls: ACK generation logic
└── Enables: FEATURE_NEW_MOCK_LOGIC_ENABLED (dependency)

FEATURE_HYBRID_AUTO_FAILOVER_ENABLED
├── Controls: Automatic failover behavior
└── Affects: Hybrid mode state transitions

FEATURE_NEW_RETRY_LOGIC_ENABLED  
├── Controls: Retry timing calculations
└── Independent: Can be tested separately
```

## Monitoring and Observability

### Required Metrics
1. **Feature Flag Status**: Current state of each flag
2. **Mode Transitions**: Count of transitions between old/new logic
3. **Error Rates**: Separate metrics for old vs new logic paths
4. **Performance**: Latency and throughput comparisons

### Log Format
```
[FEATURE] FLAG=FEATURE_NEW_OFFLINE_LOGIC_ENABLED VALUE=true CONTEXT=offline_processing
[FEATURE] FLAG=FEATURE_NEW_MOCK_LOGIC_ENABLED VALUE=false CONTEXT=mock_generation
```

## Testing Strategy

### Unit Tests
- Test each feature flag independently
- Verify gate logic correctness
- Test flag combinations and interactions

### Integration Tests  
- Test end-to-end behavior with different flag combinations
- Verify mode transitions work correctly
- Test rollback scenarios

### Canary Testing
- Deploy with flags disabled by default
- Enable flags for subset of users/devices
- Monitor metrics and errors before full rollout

## Security Considerations

- Feature flags must be validated at startup
- Invalid flag values should default to safe (legacy) behavior
- Flag changes require authentication/authorization
- Audit log all flag changes with timestamp and user

## Performance Impact

- **Legacy Logic**: Baseline performance (no additional overhead)
- **New Logic**: May have different performance characteristics
- **Flag Checks**: Minimal overhead (cached at startup)

---

*This specification ensures controlled, safe migration with clear rollback paths.*