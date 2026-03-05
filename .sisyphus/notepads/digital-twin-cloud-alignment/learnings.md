

---

# Task 4: Routing respects TWIN_CLOUD_ALIGNED flag

## Summary
Updated `addon/oig-proxy/proxy.py` to respect the `TWIN_CLOUD_ALIGNED` flag in routing logic. Added conditional logic in `_resolve_local_control_routing()` to use simplified routing when flag is True.

## Implementation

### Changes to `addon/oig-proxy/proxy.py`

1. **Import TWIN_CLOUD_ALIGNED flag** (lines ~51-55)
   - Added to existing config imports

2. **Modified `_resolve_local_control_routing()` method** (lines ~810-815)
   - Added conditional check at start of method
   - When TWIN_CLOUD_ALIGNED=True: always prefer twin if available, else local
   - Legacy mode (False): uses existing complex mode-based logic

## Key Differences Between Modes

| Aspect | Legacy Mode (False) | Cloud-Aligned Mode (True) |
|--------|---------------------|---------------------------|
| Routing Logic | Complex mode-based (ONLINE→cloud, OFFLINE→twin, HYBRID→conditional) | Simplified: always prefer twin |
| Decision Tree | 3+ branches based on mode, configured_mode, cloud availability | 2 branches: twin available? |
| Pattern | Full flexibility for different modes | Simple like ControlSettings |

## Verification

### Syntax Check
```
python3 -m py_compile addon/oig-proxy/proxy.py
# Result: PASSED
```

### Legacy Mode Test
```
TWIN_CLOUD_ALIGNED import: OK
TWIN_CLOUD_ALIGNED value: False
proxy module: OK
TWIN_CLOUD_ALIGNED used in proxy: OK
All checks passed
```

### Cloud-Aligned Mode Test
```
TWIN_CLOUD_ALIGNED (True mode): True
proxy module with TWIN_CLOUD_ALIGNED=true: OK
```

### Regression Tests
- test_proxy_modes.py: 6 passed
- test_proxy_internal.py: 15 passed

## Evidence Files
- `.sisyphus/evidence/task-4-routing.txt`

## Design Patterns Used

1. **Consistency with Task 3**: Routing uses same flag-based delegation pattern as ACK handling
2. **Simplified Decision**: Cloud-aligned mode uses simpler 2-branch logic
3. **Backward Compatibility**: Legacy mode is default, existing behavior unchanged

## Notes
- LSP diagnostics: No errors on modified file
- All existing tests pass
- Routing mirrors simplified ACK pattern from digital_twin.py
- Does not affect Cloud mode routing or OFFLINE mode behavior

## Summary
Added simplified ACK handling following the ControlSettings pattern. The `on_ack()` method now routes to either cloud-aligned or legacy implementation based on the `TWIN_CLOUD_ALIGNED` flag.

## Implementation

### Changes to `addon/oig-proxy/digital_twin.py`

1. **Import TWIN_CLOUD_ALIGNED flag**
   - Added: `from config import TWIN_CLOUD_ALIGNED`

2. **Modified `on_ack()` method** (lines ~296-305)
   - Now acts as a router based on `TWIN_CLOUD_ALIGNED` flag
   - Calls `_on_ack_cloud_aligned()` when flag is `True`
   - Calls `_on_ack_legacy()` when flag is `False` (default)

3. **Added `_on_ack_cloud_aligned()` method** (lines ~307-383)
   - Simplified conn_id validation (no INV-1/2/3)
   - Follows ControlSettings pattern:
     - Basic conn_id comparison: `delivered_conn_id vs incoming conn_id`
     - Falls back to original `conn_id` if `delivered_conn_id` is None
     - Returns `None` on mismatch (doesn't raise exception)
   - Updates `_pending_simple` dict for state tracking
   - Logs conn_id mismatches with context

4. **Added `_on_ack_legacy()` method** (lines ~385-440)
   - Preserves existing INV-1/2/3 validation
   - Raises `InvariantViolationError` on invariant violations
   - Maintains full backward compatibility

## Key Differences Between Modes

| Aspect | Legacy Mode (False) | Cloud-Aligned Mode (True) |
|--------|---------------------|---------------------------|
| Validation | INV-1/2/3 via TransactionValidator | Basic conn_id check only |
| Error Handling | Raises InvariantViolationError | Returns None on mismatch |
| State Tracking | Uses _inflight_ctx | Uses _pending_simple dict |
| Pattern | Strict invariant enforcement | Simple matching like ControlSettings |

## Verification

### Syntax Check
```bash
python3 -m py_compile addon/oig-proxy/digital_twin.py
# Result: PASSED
```

### Legacy Mode Test (Default)
```
✓ Setting queued: accepted
✓ Inflight started: tx-123
✓ Setting delivered on conn_id=1
✓ ACK on same conn_id succeeded: box_ack
```

### Cloud-Aligned Mode Test
```
✓ Setting queued: accepted
✓ Inflight started: tx-cloud-456
✓ Setting delivered on conn_id=1
✓ ACK on same conn_id succeeded: box_ack
✓ _pending_simple dict updated with transaction state
✓ ACK on wrong conn_id correctly returned None
```

## Evidence Files
- `.sisyphus/evidence/task-3-cloud-ack.txt`
- `.sisyphus/evidence/task-3-legacy-ack.txt`

## Design Patterns Used

1. **Strategy Pattern**: `on_ack()` delegates to different implementations based on configuration
2. **ControlSettings Pattern**: Cloud-aligned mode mirrors the simple ACK handling in `control_settings.py`
3. **Backward Compatibility**: Legacy mode is default, existing behavior unchanged

## Notes
- LSP diagnostics: No errors on modified file
- All existing tests remain compatible (legacy mode is default)
- `_pending_simple` dict already existed from Wave 1 (Task 2)
- No changes to timeout values or cloud mode behavior
