# Task 5: Add Twin Config Surface - Learnings

### Date: 2026-03-03

### Files Modified
- `addon/oig-proxy/config.py` - Added twin configuration with safe defaults
- `tests/test_config.py` - Added comprehensive tests for twin config

### Configuration Variables Added

#### Twin Feature Flags
- `TWIN_ENABLED` (default: `False`) - Safety default prevents twin operations without explicit enable
- `TWIN_VERBOSE_LOGGING` (default: `False`) - Reduces log noise unless explicitly requested

#### Twin Timing Configuration  
- `TWIN_ACK_DEADLINE_SECONDS` (default: `30.0`) - Time to wait for ACK before timeout
- `TWIN_APPLIED_DEADLINE_SECONDS` (default: `60.0`) - Time to wait for applied event after ACK

#### Local Control Routing
- `LOCAL_CONTROL_ROUTING` (default: `"auto"`) - Valid values: `"auto"`, `"force_twin"`, `"force_cloud"`

### Implementation Details

#### Safe Defaults Pattern
- All boolean flags default to `False` for safety
- Timeouts use sensible defaults (30s ACK, 60s applied)
- LOCAL_CONTROL_ROUTING defaults to `"auto"` to maintain existing behavior

#### Strict Validation
- `TWIN_ENABLED` uses `_get_bool_env()` with safe parsing
- Deadlines use `_get_float_env()` with fallback to defaults on invalid input
- `LOCAL_CONTROL_ROUTING` uses `_get_str_env()` with explicit valid values list

#### Error Handling
- Invalid boolean values (e.g., "maybe") fall back to `False`
- Invalid numeric values (e.g., "invalid") fall back to default timeouts
- Invalid routing values fall back to `"auto"`

### Test Coverage

#### Test Functions Created
- `test_twin_config_safe_defaults()` - Verifies safe defaults without env vars
- `test_twin_config_valid_values()` - Tests parsing of valid environment values
- `test_twin_config_invalid_values_fallback()` - Tests fallback on invalid inputs
- `test_local_control_routing_validation()` - Tests routing validation edge cases

#### Verification Commands
```bash
# Run twin-specific tests
PYTHONPATH=addon/oig-proxy pytest -q tests/test_config.py -k twin --maxfail=1
# Returns exit code 0 (3 passed)

# Run routing validation tests
PYTHONPATH=addon/oig-proxy pytest -q tests/test_config.py -k local_control_routing --maxfail=1
# Returns exit code 0 (1 passed)

# Run all config tests
PYTHONPATH=addon/oig-proxy pytest -q tests/test_config.py
# Returns exit code 0 (7 passed)
```

### Configuration Usage

#### Enable Twin Operations
```bash
export TWIN_ENABLED=true
export TWIN_ACK_DEADLINE_SECONDS=45
export TWIN_APPLIED_DEADLINE_SECONDS=90
export TWIN_VERBOSE_LOGGING=true
export LOCAL_CONTROL_ROUTING=force_twin
```

#### Force Local Routing
```bash
export LOCAL_CONTROL_ROUTING=force_twin  # Always use twin for local control
export LOCAL_CONTROL_ROUTING=force_cloud # Always use cloud when available
export LOCAL_CONTROL_ROUTING=auto        # Automatic selection (default)
```

### Integration Notes

#### Dependency on Tasks 1-4
- Twin config supports the digital twin state machine from Task 4
- Configuration enables/disables twin functionality safely
- LOCAL_CONTROL_ROUTING will be used in Task 11 for delivery policy

#### Safety Considerations
- `TWIN_ENABLED` defaulting to `False` prevents accidental twin activation
- Strict validation prevents runtime errors from invalid configuration
- Tests cover all edge cases including malformed environment variables

#### Files Status
- ✅ `addon/oig-proxy/config.py` - Modified with twin configuration
- ✅ `tests/test_config.py` - Modified with comprehensive tests
- ✅ Verification commands pass successfully

---


---

# Task 6: Poll-Driven Queue Delivery (IsNewSet) - Learnings

### Date: 2026-03-03

### Files Modified
- `addon/oig-proxy/digital_twin.py` - Implemented poll-driven delivery
- `tests/test_twin_poll_delivery.py` - Comprehensive tests for poll delivery

### Implementation Details

#### Poll-Driven Delivery Pattern
- Commands are ONLY delivered on IsNewSet polls (never pushed unsolicited)
- `on_poll()` checks table_name to determine behavior:
  - `IsNewSet`: Deliver pending setting or return END
  - Other tables: Return ack with no frame_data

#### Key Methods Added
1. `_deliver_on_is_new_set()` - Core delivery logic:
   - Dequeues from queue if no inflight exists
   - Delivers pending setting with frame_data
   - Returns END frame when idle

2. `_build_delivery_response()` - Frame building:
   - Builds Setting frame from pending state
   - Sets delivered_conn_id for INV-1 validation
   - Starts ACK timeout after delivery

3. `_build_setting_frame()` - Frame construction:
   - Mirrors control_settings.py frame structure
   - Includes device_id, tbl_name, tbl_item, new_value
   - Generates valid CRC

#### END Emission
- Uses `build_end_time_frame()` from oig_frame module
- Includes Time, UTCTime, ToDo=GetActual tags
- Emitted when no pending commands exist

#### Frame Building Pattern
```python
inner = (
    f"<ID>{msg_id}</ID>"
    f"<ID_Device>{device_id}</ID_Device>"
    f"<ID_Set>{id_set}</ID_Set>"
    # ... more fields ...
    f"<ver>{random_ver:05d}</ver>"
)
return build_frame(inner, add_crlf=True)
```

### Test Coverage

#### Happy Path Tests
- `test_on_poll_returns_end_when_idle` - END emission
- `test_on_poll_delivers_pending_setting` - Setting delivery
- `test_on_poll_sets_delivered_conn_id` - INV-1 validation
- `test_on_poll_dequeues_from_queue` - Queue processing

#### No Unsolicited Push Tests
- `test_unsolicited_push_not_sent_on_non_poll` - No auto-push
- `test_setting_only_delivered_on_is_new_set` - Poll type check
- `test_delivery_requires_explicit_poll` - Explicit poll required

#### Frame Building Tests
- `test_frame_contains_device_id` - Device ID in frame
- `test_frame_contains_tbl_name_and_item` - Table/item in frame
- `test_frame_has_valid_crc` - CRC validation
- `test_frame_has_reason_setting` - Reason tag

#### END Emission Tests
- `test_end_frame_has_time_tags` - Time/UTCTime tags
- `test_end_frame_has_getactual_todo` - ToDo=GetActual
- `test_idle_after_disconnect_returns_end` - Post-disconnect END

### Verification Commands
```bash
# All poll delivery tests
PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_poll_delivery.py --maxfail=1
# Returns: 18 passed

# Unsolicited push tests only
PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_poll_delivery.py -k unsolicited --maxfail=1
# Returns: 3 passed
```

### Integration Notes

#### Configuration Required
- `DigitalTwinConfig.device_id` must be set for frame building
- Default is "AUTO" (placeholder)

#### Protocol Requirements
- BOX ignores Setting frames outside IsNewSet poll-response context
- Commands must be queued and delivered on next IsNewSet poll
- No unsolicited command push is allowed

#### Files Status
- ✅ `addon/oig-proxy/digital_twin.py` - Poll delivery implemented
- ✅ `tests/test_twin_poll_delivery.py` - Comprehensive tests
- ✅ All verification commands pass

---

# Task 7: ACK/NACK Correlation - Learnings

### Date: 2026-03-03

### Files Modified
- `tests/test_twin_ack_correlation.py` - Comprehensive tests for ACK/NACK correlation

### Implementation Details

The `on_ack()` method was already implemented in `digital_twin.py` (lines 265-326).
It enforces INV-1 (Connection Ownership) and INV-2 (Session Transaction) invariants.

#### INV-1 Validation Flow
1. ACK arrives with conn_id from BOX
2. Validate conn_id matches delivered_conn_id in TransactionContext
3. If mismatch: raise `InvariantViolationError("INV-1", ...)`
4. If match: proceed to process ACK/NACK

#### ACK vs NACK Handling
- **ACK (ack=True)**: 
  - Transitions state to `BOX_ACK` stage
  - Starts applied timeout task
  - Returns status="box_ack"
  
- **NACK (ack=False)**:
  - Transitions state to `ERROR` stage
  - Clears inflight immediately
  - Returns status="error", error="box_nack"

#### Key Validation Pattern
```python
async def on_ack(self, dto: OnAckDTO) -> TransactionResultDTO | None:
    async with self._lock:
        if self._inflight is None or self._inflight.tx_id != dto.tx_id:
            return None

        ctx = self._inflight_ctx
        if ctx is None:
            return None

        # INV-1: Connection Ownership
        ok, err = TransactionValidator.validate_inv1(ctx, dto.conn_id)
        if not ok:
            raise InvariantViolationError("INV-1", err, ctx)

        # INV-2: Session Transaction
        ok, err = TransactionValidator.validate_inv2(ctx, self.session_id)
        if not ok:
            raise InvariantViolationError("INV-2", err, ctx)

        # Process ACK/NACK...
```

### Test Coverage

#### Correct Connection Tests
- `test_ack_on_same_conn_completes_to_box_ack_stage` - ACK on delivered conn succeeds
- `test_ack_sets_delivered_conn_id_in_context` - Context tracking
- `test_ack_on_conn_0_matches_delivered_conn_0` - Edge case: conn_id=0
- `test_ack_on_different_conn_id_than_original_request` - Uses delivered_conn_id

#### Wrong Connection Tests
- `test_ack_on_wrong_conn_raises_invariant_violation` - INV-1 error raised
- `test_wrong_conn_ack_never_completes_transaction` - State preserved
- `test_wrong_conn_ack_preserves_inflight_state` - Inflight not cleared
- `test_subsequent_correct_ack_succeeds_after_wrong_conn_failure` - Retry succeeds

#### NACK Tests
- `test_nack_on_correct_conn_fails_deterministically` - NACK returns error
- `test_nack_clears_inflight` - Inflight cleared on NACK
- `test_nack_on_wrong_conn_raises_invariant_violation` - INV-1 still enforced
- `test_nack_includes_reason_in_result` - error="box_nack"

#### Edge Cases
- `test_ack_with_no_inflight_returns_none` - No matching transaction
- `test_ack_with_wrong_tx_id_returns_none` - tx_id mismatch
- `test_ack_validates_session` - INV-2 session validation

### Verification Commands
```bash
# Correct connection tests
PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_ack_correlation.py -k correct_conn --maxfail=1
# Returns: 1 passed, 14 deselected

# Wrong connection tests
PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_ack_correlation.py -k wrong_conn --maxfail=1
# Returns: 5 passed, 10 deselected

# All tests
PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_ack_correlation.py --maxfail=1
# Returns: 15 passed
```

### Integration Notes

#### Dependencies
- Task 4: Transaction state machine with TransactionContext
- Task 6: Poll-driven delivery sets delivered_conn_id

#### Invariant Guarantees
- INV-1 ensures ACK/NACK only processed on correct connection
- Wrong-conn ACK never completes transaction (state preserved)
- NACK deterministically fails with error reason

#### Files Status
- ✅ `addon/oig-proxy/digital_twin.py` - on_ack() already implemented
- ✅ `tests/test_twin_ack_correlation.py` - 15 comprehensive tests
- ✅ All verification commands pass

---

# Task 11: Integrate Twin Into Proxy Routing - Learnings

### Date: 2026-03-03

### Files Modified
- `addon/oig-proxy/proxy.py` - Completed twin routing integration in main connection loop

### Key Routing Learning
- Local-control poll routing (`IsNewSet`, `IsNewWeather`, `IsNewFW`) must be evaluated before mode-specific cloud/offline branches.
- Without early routing, `LOCAL_CONTROL_ROUTING=force_twin` is ignored in ONLINE mode because the frame is forwarded to cloud before twin dispatch can run.

### Behavior Added
- Unified pre-branch poll routing gate in `_handle_box_connection`:
  - Resolve routing once for local-control poll tables.
  - Dispatch through twin whenever routing resolves to `"twin"`.
  - If `force_twin` is set and twin has no payload (e.g., non-`IsNewSet` poll), send local ACK fallback via `_process_frame_offline` instead of forwarding to cloud.
- Existing HYBRID failover behavior remains backward compatible:
  - HYBRID + cloud probe disabled still goes through offline handling.
  - AUTO routing in offline/failover contexts still falls back safely to local ACK when twin does not provide a frame.

### Verification
```bash
PYTHONPATH=addon/oig-proxy pytest tests/test_proxy_flow.py tests/test_proxy_modes.py -k "routing or hybrid or force_twin or force_cloud or dispatch"
```

Result: 18 passed, 0 failed (focused proxy routing coverage).

---

## Task 12: Integrate Control Pipeline Lifecycle - Learnings

### Date: 2026-03-03

### Files Modified
- `addon/oig-proxy/control_settings.py` - Added connection-aware ACK handling
- `addon/oig-proxy/control_pipeline.py` - Added twin routing, session validation, and lifecycle binding
- `addon/oig-proxy/proxy.py` - Added twin ACK/event handling integration

### Key Fixes

#### INV-1: Connection Ownership in maybe_handle_ack
- Added `delivered_conn_id` validation before processing ACK
- If `pending.delivered_conn_id` doesn't match incoming `conn_id`, ACK is ignored
- Prevents cross-session ACK clearing when BOX reconnects

```python
delivered_conn_id = pending.get("delivered_conn_id")
if delivered_conn_id is not None and delivered_conn_id != conn_id:
    logger.info("CONTROL: ACK ignored — conn_id mismatch ...")
    return False
```

#### INV-2: Session Transaction in on_box_setting_ack
- Added session validation before processing ACK in pipeline
- If `inflight._session_id` doesn't match `self.session_id`, ACK is ignored
- Prevents stale ACK callbacks from affecting new sessions

```python
inflight_session = tx.get("_session_id")
if inflight_session is not None and inflight_session != self.session_id:
    logger.debug("CONTROL: ACK ignored — session mismatch ...")
    return
```

### Twin Routing Integration

#### Pipeline Delegates to Twin When Routing Resolves
- `start_inflight()` checks `_resolve_twin_routing()` before delegating
- If routing == "twin", command is queued via `_start_inflight_via_twin()`
- Pipeline tracks ` _twin_inflight_tx_id` for correlation

#### Result Callback Binding
- `on_twin_result()` handles twin transaction results
- Maps twin states (box_ack, applied, completed, error) to pipeline results
- Ensures equivalent terminal statuses between cloud and twin paths

### Test Coverage
- All 16 tests in `test_proxy_control_ack.py` pass
- All 33 tests in twin tests pass
- All 27 tests in proxy flow/modes pass

### Verification Commands
```bash
PYTHONPATH=addon/oig-proxy pytest tests/test_proxy_control_ack.py -v
# Returns: 16 passed

PYTHONPATH=addon/oig-proxy pytest tests/test_twin_poll_delivery.py tests/test_twin_ack_correlation.py -v
# Returns: 33 passed

PYTHONPATH=addon/oig-proxy pytest tests/test_proxy_flow.py tests/test_proxy_modes.py -v
# Returns: 27 passed
```

---

## Task 13: Replace Legacy ACK Replacement Branch - Learnings

### Date: 2026-03-03

### Files Modified
- `addon/oig-proxy/proxy.py` - Unified ACK generation through control pipeline
- `tests/test_proxy_main_loop.py` - Fixed test helper to match production interfaces

### Key Changes

#### Routing Logic Update
Updated `_resolve_local_control_routing()` to return "twin" in OFFLINE mode when twin is enabled:
- Previously: OFFLINE mode always returned "local"
- Now: OFFLINE mode returns "twin" if `TWIN_ENABLED` and twin instance exists
- This ensures all local control commands use the unified pipeline

#### Legacy ACK Replacement Removal
Removed duplicate ACK replacement from `_process_frame_offline()`:
- The original code had ACK replacement logic that would deliver `pending_frame` as an ACK replacement
- This was duplicated with `cloud_forwarder.forward_ack_to_box()` which does the same for cloud mode
- Now: `_process_frame_offline()` only handles fallback delivery when twin is unavailable (`self._twin is None`)

### Unified ACK Generation Paths

#### Twin Path (Primary)
1. Pipeline receives command via MQTT
2. `start_inflight()` routes to twin via `_start_inflight_via_twin()`
3. Command stored in twin's queue
4. On IsNewSet poll: `_dispatch_local_control_via_twin()` calls `twin.on_poll()`
5. Twin delivers command and returns frame_data
6. ACK handling via `twin.on_ack()` and pipeline's `on_twin_result()`

#### Cloud Path (When cloud connected)
1. Pipeline receives command via MQTT
2. `start_inflight()` routes to cloud via `send_to_box()`
3. Command stored in `control_settings.pending_frame`
4. On cloud ACK: `cloud_forwarder.forward_ack_to_box()` does ACK replacement
5. ACK handling via `control_settings.maybe_handle_ack()` and pipeline's `on_box_setting_ack()`

#### Offline Fallback (When twin unavailable)
1. Pipeline receives command via MQTT
2. `start_inflight()` routes to cloud/local via `send_to_box()`
3. Command stored in `control_settings.pending_frame`
4. On IsNewSet poll: `_process_frame_offline()` delivers pending_frame
5. Condition: `self._twin is None` (twin not enabled)
6. ACK handling via `control_settings.maybe_handle_ack()`

### Key Insight
The key to unification is that **twin-enabled deployments route all local control through the twin queue**, while **twin-disabled deployments use the original `pending_frame` mechanism**. The routing logic in `_resolve_local_control_routing()` determines which path to use.

### Test Coverage
```bash
# All ACK handling tests
PYTHONPATH=addon/oig-proxy pytest tests/test_proxy_control_ack.py -v
# Returns: 16 passed

# Proxy flow and routing tests
PYTHONPATH=addon/oig-proxy pytest tests/test_proxy_flow.py tests/test_proxy_modes.py -v
# Returns: 27 passed

# Main loop tests
PYTHONPATH=addon/oig-proxy pytest tests/test_proxy_main_loop.py -v
# Returns: 11 passed

# Twin tests
PYTHONPATH=addon/oig-proxy pytest tests/test_twin_poll_delivery.py tests/test_twin_ack_correlation.py -v
# Returns: 33 passed
```

### Test Helper Fix
Updated `_make_proxy()` in `test_proxy_main_loop.py` to:
- Add `get_extra_info()` method to `DummyWriter`
- Initialize `_twin = None` attribute
- Initialize `_ctrl` mock with `maybe_start_next`
- Initialize `_box_conn_lock` and `_conn_seq`

### Files Status
- ✅ `addon/oig-proxy/proxy.py` - ACK unification complete
- ✅ `tests/test_proxy_main_loop.py` - Test helper fixed
- ✅ All 87 related tests pass

---

## Task 14: Add Twin End-to-End Setting Roundtrip Tests - Learnings

### Date: 2026-03-03

### Files Created
- `tests/test_twin_e2e_roundtrip.py` - Comprehensive E2E tests for twin transaction lifecycle

### Test Categories Created

#### 1. Happy Path Roundtrip Tests (4 tests)
- `test_full_roundtrip_poll_to_completion` - Complete lifecycle: queue -> poll -> ACK -> tbl_event -> complete
- `test_multiple_sequential_roundtrips` - Multiple commands completing in sequence
- `test_end_emission_after_completion` - END frame emission after all completed
- `test_nack_short_circuits_to_error` - NACK immediately errors without tbl_event

#### 2. Cross-Session Failure Tests (6 tests)
- `test_disconnect_clears_delivered_but_unacked` - Disconnect mid-transaction
- `test_ack_on_wrong_connection_raises_inv1` - INV-1 violation on wrong conn ACK
- `test_reconnect_allows_new_transaction_after_error` - Recovery after disconnect
- `test_session_change_invalidates_old_transaction` - INV-2 session validation
- `test_ack_on_initiation_conn_when_not_yet_delivered` - ACK on initiation conn
- `test_ack_on_wrong_conn_when_not_yet_delivered_fails` - Wrong conn fails even before delivery

#### 3. Timeout No False Positive Tests (5 tests)
- `test_ack_timeout_ignored_after_completion` - Timeout ignored after finish
- `test_applied_timeout_ignored_after_completion` - Applied timeout ignored
- `test_timeout_after_session_change_ignored` - INV-3 validation prevents stale timeout
- `test_delivered_setting_remains_on_ack_timeout` - Delivered settings not marked DEFERRED
- `test_concurrent_timeout_and_ack_no_race` - No race condition

#### 4. tbl_events Correlation Tests (4 tests)
- `test_tbl_event_matches_inflight_setting` - Matching event progresses to APPLIED
- `test_tbl_event_ignored_for_mismatched_table` - Wrong table ignored
- `test_tbl_event_ignored_for_non_setting_event` - Non-Setting events ignored
- `test_tbl_event_ignored_when_no_inflight` - No crash when no inflight

#### 5. Full Lifecycle Integration Tests (2 tests)
- `test_complete_lifecycle_with_all_stages` - All stage transitions verified
- `test_error_recovery_allows_new_transactions` - Error doesn't block new transactions

### Key Insights

#### INV-1 Connection Ownership Behavior
- When `delivered_conn_id` is None, validation falls back to `conn_id` (initiation connection)
- This means even before delivery, the transaction is bound to a connection
- ACK must match either `delivered_conn_id` (if set) or `conn_id` (if not delivered)

#### ACK Timeout Behavior
- ACK timeout only marks as DEFERRED when `delivered_at_mono` is None
- Once setting is delivered (via poll), timeout doesn't mark as DEFERRED
- This prevents premature deferral of delivered settings

#### tbl_events Matching Logic
- Events must match both `tbl_name` AND `tbl_item` to progress to APPLIED
- Event type must be "Setting" (case-sensitive)
- Non-matching events are silently ignored (return None)

### Verification Commands
```bash
# All E2E roundtrip tests
PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_e2e_roundtrip.py --maxfail=1
# Returns: 22 passed

# All twin tests combined
PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_*.py --maxfail=1
# Returns: 55 passed (18 + 15 + 22)

# Related proxy tests
PYTHONPATH=addon/oig-proxy pytest -q tests/test_proxy_control_ack.py tests/test_proxy_flow.py tests/test_proxy_modes.py --maxfail=1
# Returns: 43 passed
```

### Files Status
- ✅ `tests/test_twin_e2e_roundtrip.py` - Created with 22 comprehensive tests
- ✅ All existing twin tests pass (55 total)
- ✅ All related proxy tests pass (43 total)

---

## Task 16: Big-Bang Safety Switch + Startup Guards - Learnings

### Date: 2026-03-04

### Files Modified
- `addon/oig-proxy/config.py` - Added kill-switch config and strict startup guard validation
- `addon/oig-proxy/proxy.py` - Added runtime twin kill-switch behavior for routing
- `addon/oig-proxy/main.py` - Enforced startup guard validation before proxy startup
- `tests/test_config.py` - Added startup guard test coverage
- `tests/test_proxy_flow.py` - Added kill-switch routing tests

### Runtime Safety Behavior
- New `TWIN_KILL_SWITCH` env flag acts as emergency rollback switch.
- When kill-switch is enabled, twin route is deterministically disabled in routing resolution.
- Runtime toggle path is available through `OIGProxy.set_twin_kill_switch(enabled)`.

### Startup Guard Rules
- Startup now blocks invalid twin safety config by raising `ValueError` from `validate_startup_guards()`.
- Guard checks:
  - `TWIN_ACK_DEADLINE_SECONDS > 0`
  - `TWIN_APPLIED_DEADLINE_SECONDS > 0`
  - `TWIN_APPLIED_DEADLINE_SECONDS >= TWIN_ACK_DEADLINE_SECONDS`
  - `LOCAL_CONTROL_ROUTING=force_twin` requires `TWIN_ENABLED=true` and `TWIN_KILL_SWITCH=false`

### Verification Commands
```bash
PYTHONPATH=addon/oig-proxy pytest -q tests/test_proxy_flow.py -k twin_flag --maxfail=1
PYTHONPATH=addon/oig-proxy pytest -q tests/test_config.py -k twin_startup --maxfail=1
PYTHONPATH=addon/oig-proxy pytest -q tests/test_proxy_flow.py -k "twin_flag or force_twin" --maxfail=1
PYTHONPATH=addon/oig-proxy pytest -q tests/test_config.py -k "twin or local_control_routing" --maxfail=1
```

### Result
- Kill-switch routing behavior is deterministic.
- Invalid safety config now blocks startup before partial runtime initialization.

---

## Task 15: Replay Resilience for Twin Inflight - Learnings

### Date: 2026-03-04

### Files Modified
- `addon/oig-proxy/digital_twin.py` - Added replay buffer, duplicate prevention, and reconnect recovery
- `addon/oig-proxy/twin_state.py` - Added replay_count to PendingSettingState
- `tests/test_twin_replay_resilience.py` - Created comprehensive replay resilience tests
- `tests/test_twin_e2e_roundtrip.py` - Updated disconnect test for replay buffer behavior

### Key Components Added

#### ReplayEntry Dataclass
Tracks transactions that need replay after disconnect:
- `dto`: Original QueueSettingDTO
- `delivered_at_mono`: Whether setting was delivered before disconnect
- `replay_count`: Number of replay attempts
- `original_conn_id`: Original connection ID
- `last_error`: Last error reason

#### DigitalTwin New Attributes
- `_replay_buffer: deque[ReplayEntry]` - Buffer for disconnected transactions
- `_completed_tx_ids: set[str]` - Set of successfully completed tx_ids for dedup
- `_replay_tx_counts: dict[str, int]` - Tracks replay counts across reconnect cycles

#### New Methods
- `on_reconnect(conn_id)` - Moves replay buffer items to main queue
- `get_replay_buffer_length()` - Returns replay buffer size
- `get_replay_buffer_snapshot()` - Returns snapshot of replay buffer
- `is_tx_completed(tx_id)` - Checks if tx_id is in completed set

### Replay Resilience Flow

1. **Disconnect Detection**: `on_disconnect()` moves delivered-but-unacked transactions to replay buffer
2. **Completed Tracking**: Successfully completed tx_ids added to `_completed_tx_ids`
3. **Reconnect Recovery**: `on_reconnect()` moves replay buffer to queue, skipping completed tx_ids
4. **Duplicate Prevention**: `_completed_tx_ids` set prevents re-delivery of already-completed transactions
5. **Max Attempts**: Transactions exceeding `max_replay_attempts` return error instead of re-queuing

### Configuration Options

```python
DigitalTwinConfig(
    max_replay_attempts=3,  # Max replay attempts before error
    replay_delay_s=1.0,     # Delay before replay on reconnect (future use)
)
```

### Test Coverage

#### Test Categories (19 tests)
1. **Replay Buffer Tests** (4 tests)
   - Delivered but unacked moved to replay buffer
   - Not-delivered transactions stay in queue
   - Multiple disconnects accumulate
   - Entry contains original details

2. **Duplicate Prevention Tests** (3 tests)
   - Completed transactions not replayed
   - NACKed transactions can be replayed
   - Only successful completions tracked

3. **State Recovery Tests** (5 tests)
   - Reconnect moves replay to queue
   - Replayed transaction on new connection
   - Replay count incremented
   - Full roundtrip after reconnect

4. **Max Replay Attempts Tests** (2 tests)
   - Exceed max returns error
   - Successful completion resets count

5. **Snapshot Integration Tests** (3 tests)
   - Snapshot includes replay_buffer_length
   - Snapshot includes completed_tx_count
   - clear_all clears replay buffer

6. **Edge Cases Tests** (2 tests)
   - Reconnect with empty buffer
   - Multiple transactions replayed in FIFO order

### Key Insights

#### Replay Count Preservation
- `replay_count` is stored in `PendingSettingState` 
- Preserved across mark_delivered(), mark_deferred(), transition_to()
- `_replay_tx_counts` dict bridges queue → inflight transition

#### FIFO Order Maintenance
- When moving replay buffer to queue, use `append()` (not `appendleft`)
- Collect entries first, then add in order to maintain FIFO

#### Completed Set Pruning
- `_prune_completed_tx_ids()` limits set size to prevent memory growth
- Keeps most recent 500 entries when exceeding 1000

### Verification Commands
```bash
# Replay resilience tests
PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_replay_resilience.py --maxfail=1
# Returns: 19 passed

# All twin tests combined
PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_*.py --maxfail=1
# Returns: 74 passed (18 + 15 + 22 + 19)

# Related proxy tests
PYTHONPATH=addon/oig-proxy pytest -q tests/test_proxy_control_ack.py tests/test_proxy_flow.py tests/test_proxy_modes.py --maxfail=1
# Returns: 43 passed
```

### Files Status
- ✅ `addon/oig-proxy/digital_twin.py` - Replay resilience implemented
- ✅ `addon/oig-proxy/twin_state.py` - replay_count added to PendingSettingState
- ✅ `tests/test_twin_replay_resilience.py` - Created with 19 comprehensive tests
- ✅ All twin tests pass (74 total)
- ✅ All related proxy tests pass (43 total)

---

## Task 18: Final Cutover Validation + Legacy Cleanup - Learnings

### Date: 2026-03-04

### Files Examined
- `addon/oig-proxy/proxy.py` - Main routing logic, twin dispatch, ACK handling
- `addon/oig-proxy/cloud_forwarder.py` - Cloud ACK replacement path
- `addon/oig-proxy/control_settings.py` - Local ACK fallback path
- `addon/oig-proxy/config.py` - Twin configuration and startup guards
- `addon/oig-proxy/main.py` - Startup validation entry point

### Validation Results

#### No Dead Legacy ACK References Found
- `pending_frame` is NOT dead code - it's used for:
  1. Cloud path: ACK replacement in `cloud_forwarder.py` (lines 474-512)
  2. Offline fallback path: When twin is unavailable in `proxy.py` (lines 721-756)
- All ACK paths are intentional and necessary for hybrid/failover support

#### Final Routing Contract Conformance
The routing contract conforms to hybrid+failover+twin policy as follows:

**Routing Resolution (`_resolve_local_control_routing()`)**:
1. `force_twin` override:
   - If twin routing available → "twin"
   - Otherwise → "local" (fallback)

2. `force_cloud` override:
   - If cloud available (should_try_cloud) → "cloud"
   - Otherwise → "local" (fallback)

3. OFFLINE mode (or offline-configured):
   - If twin routing available → "twin"
   - Otherwise → "local" (fallback)

4. ONLINE mode:
   - Always → "cloud"

5. HYBRID mode:
   - If cloud NOT available (failover):
     - If twin routing available → "twin"
     - Otherwise → "local"
   - Otherwise → "cloud"

**Twin Routing Availability Check (`_is_twin_routing_available()`)**:
```python
return (not self._twin_kill_switch) and self._twin_enabled and self._twin is not None
```

This ensures twin routing is only used when:
- Kill-switch is NOT enabled
- TWIN_ENABLED=true
- Twin instance exists

### Emergency Rollback Controls Verified
The TWIN_KILL_SWITCH mechanism is intact and functional:

1. **Config flag**: `TWIN_KILL_SWITCH = _get_bool_env("TWIN_KILL_SWITCH", False)`
2. **Startup guard**: `validate_startup_guards()` blocks startup if:
   - `LOCAL_CONTROL_ROUTING=force_twin` with `TWIN_ENABLED=false`
   - `LOCAL_CONTROL_ROUTING=force_twin` with `TWIN_KILL_SWITCH=true`
3. **Runtime toggle**: `set_twin_kill_switch(enabled)` allows dynamic enable/disable
4. **Routing gate**: `_is_twin_routing_available()` checks kill-switch before routing

### Test Coverage
All key tests pass:
- `tests/test_proxy_flow.py` - 23 tests (routing + kill-switch)
- `tests/test_proxy_modes.py` - 6 tests (hybrid mode)
- `tests/test_proxy_control_ack.py` - 16 tests (ACK handling)
- `tests/test_twin_poll_delivery.py` - 18 tests (poll delivery)
- `tests/test_twin_ack_correlation.py` - 15 tests (ACK correlation)
- `tests/test_config.py` - 10 tests (config validation + startup guards)

Total: 89 key routing tests pass.

### Known Limitations (Intentional)
- `test_restore_from_snapshot_rebuilds_state` is a RED test - `restore_from_snapshot()` not fully implemented
- This is intentional TDD pattern for future feature

### Files Status
- ✅ `addon/oig-proxy/proxy.py` - No dead code, routing contract validated
- ✅ `addon/oig-proxy/cloud_forwarder.py` - ACK replacement path intact
- ✅ `addon/oig-proxy/control_settings.py` - Local fallback path intact
- ✅ `addon/oig-proxy/config.py` - Startup guards and kill-switch active
- ✅ `addon/oig-proxy/main.py` - Startup validation entry point
- ✅ All 89 key routing tests pass
- ✅ Emergency rollback controls verified

## 2026-03-04 04:17:25Z - F1 audit learnings
- Core twin regression suites are green: test_proxy_control_ack.py, test_proxy_flow.py, test_proxy_cloud_session.py, plus focused twin suites (test_twin_ack_correlation.py, test_twin_poll_delivery.py, test_twin_e2e_roundtrip.py, test_twin_replay_resilience.py).
- Conn-bound ACK correlation is enforced in twin path via DigitalTwin.on_ack INV-1 checks and raises InvariantViolationError on mismatch (addon/oig-proxy/digital_twin.py).
- Local control routing is centralized through OIGProxy._resolve_local_control_routing with auto|force_twin|force_cloud override and kill-switch awareness (addon/oig-proxy/proxy.py).

## Task 17: Regression Testing Patterns (2026-03-04)

### Pattern: Test Helper Attributes Must Match Production Code

When test helpers bypass `__init__` to create objects, they must be updated whenever new instance attributes are added to the production code. The `_local_control_routing` attribute was added for digital twin support but test helpers didn't include it.

**Best Practice**: When adding new instance attributes to production classes, search for all test helper functions that create instances of that class and update them.

### Pattern: RED Test Marking

Tests that are intentionally designed to fail (RED tests for future functionality) should be marked with `@pytest.mark.xfail` to:
1. Not break CI pipelines
2. Clearly communicate these are expected failures
3. Track progress when they start passing (xpass)

### Pattern: Sentinel Objects in Async Tests

When testing async code that expects stream objects, using bare `object()` sentinels can cause issues if the code tries to call methods on them. Use `None` or proper mock objects with required methods instead.

### Key Files for Digital Twin Routing
- `proxy.py:_resolve_local_control_routing()` - Main routing logic
- `proxy.py:_is_twin_routing_available()` - Checks twin availability
- `control_pipeline.py:_resolve_twin_routing()` - Pipeline routing helper
