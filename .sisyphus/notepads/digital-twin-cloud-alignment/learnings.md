

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

---

# Task: Fix digital twin and telemetry tests after API refactor

## Summary
Updated test expectations to match current runtime behavior without touching implementation code. Focus areas were telemetry client error handling (now catches narrower exception classes), safe test setup for read-only `/data` environments, and digital twin RED test stability under improved implementation.

## Learnings
- Telemetry client now catches specific network/client exceptions (`OSError`, `ConnectionError`, `TimeoutError`, `AttributeError`), so tests expecting graceful failure must raise one of these instead of generic `Exception`/`RuntimeError`.
- `TelemetryBuffer` initialization path calls `db_utils.init_sqlite_db` (not direct `sqlite3.connect`), so failure-path tests must patch `db_utils.init_sqlite_db`.
- Tests creating `TelemetryClient` without patching `MQTT_AVAILABLE` can fail in local CI/dev due to default buffer path `/data/telemetry_buffer.db` on read-only filesystems; disabling MQTT for these unit tests avoids environment-dependent failures.
- Previously class-level `xfail` on RED digital twin tests became stale as implementation progressed, producing XPASS noise; moving `xfail` to only the still-incomplete restore test keeps suite intent accurate.
- Added file-level pyright directives in tests that intentionally import addon modules via test PYTHONPATH to keep diagnostics clean while preserving runtime behavior.

---

# Task: Fix twin E2E/replay and cloud session unit tests after API drift

## Summary
Adjusted four test files to match current twin/cloud behavior after refactors (auto-queued SA in twin flow and offline fallback path in cloud forwarder), without touching runtime implementation.

## Learnings
- `DigitalTwin.on_tbl_event()` now auto-queues an SA command after non-SA setting apply; tests expecting immediate END/empty queue must use SA as the primary test setting when asserting no follow-up work.
- Replay dedup tests should also use SA in completed flows, otherwise the auto-generated SA can alter replay-buffer expectations.
- `CloudForwarder.fallback_offline()` now routes through `_respond_local_offline()` rather than `_process_frame_offline()`, so session tests should assert `_respond_local_offline` calls.
- Test stream-writer doubles used in proxy/cloud tests must expose `get_extra_info()` (at least `peername`) to satisfy current proxy offline ACK path.
- For these test modules, file-level `# pyright: reportMissingImports=false` keeps diagnostics clean when imports rely on `PYTHONPATH=addon/oig-proxy` at test runtime.

## Verification
- `PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_e2e_roundtrip.py tests/test_twin_replay_resilience.py tests/test_proxy_cloud_session.py tests/test_proxy_box_session.py`
- Result: `59 passed`

---

# Task: Twin inflight deterministic finalization (Blind Branch #2)

## Learnings
- Centralizing inflight cleanup through a single locked helper prevents drift between ACK/NACK, tbl_event, and timeout paths.
- Releasing inflight on `applied` avoids queue starvation when no explicit completion callback follows table-event confirmation.
- Timeout handlers should finalize terminal states (deferred/error) and clear `_inflight` immediately to guarantee next queue item can proceed.
- Existing tests encoded non-terminal expectations for `applied` and timeout error stage; they must be aligned with deterministic terminal-release semantics.
