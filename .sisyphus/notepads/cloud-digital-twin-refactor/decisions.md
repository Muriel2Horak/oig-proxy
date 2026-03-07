

---

## Task 4: Implement Conn-Bound Transaction State Machine - Decisions

### Date: 2026-03-03

### Decision 1: Immutable TransactionContext
**Context**: Need to capture transaction context at creation time
**Decision**: Use immutable dataclass with `with_*` methods that return new instances
**Rationale**: 
- Prevents accidental mutation of context
- Clear audit trail of context changes
- Thread-safe by design
**Alternatives Considered**: Mutable context with update methods

### Decision 2: Atomic State Transitions
**Context**: Need to enforce valid state transitions
**Decision**: Add `transition_to()` with `validate_from` parameter
**Rationale**:
- Prevents invalid state transitions at runtime
- Clear documentation of valid transitions
- Early error detection
**Alternatives Considered**: Direct attribute assignment with validation

### Decision 3: Separate TransactionValidator Class
**Context**: Need to validate invariants from multiple places
**Decision**: Static methods in TransactionValidator class
**Rationale**:
- Reusable validation logic
- Consistent error messages
- Easy to test in isolation
**Alternatives Considered**: Instance methods on TransactionContext

### Decision 4: DigitalTwin Stores Context Alongside State
**Context**: Need to validate invariants during callbacks
**Decision**: Store `_inflight_ctx` parallel to `_inflight`
**Rationale**:
- Context available for timeout validation
- Context updated when state changes
- Clear separation of state and context
**Alternatives Considered**: Embed context in PendingSettingState

### Decision 5: RED Tests for Unimplemented Features
**Context**: Need tests that fail because features aren't implemented
**Decision**: Add tests for frame building and TwinAdapterProtocol compliance
**Rationale**:
- Documents expected future behavior
- Clear signal when features are complete
- Follows TDD principles
**Alternatives Considered**: Only test implemented features

---

## Task 11: Integrate Twin Into Proxy Routing - Decisions

### Date: 2026-03-03

### Decision 1: Route Local-Control Polls Before Mode Branching
**Context**: `force_twin` override was being bypassed in ONLINE path due mode-first branch order.
**Decision**: Add a pre-branch gate in `_handle_box_connection` for local-control poll tables and resolve routing there first.
**Rationale**:
- Ensures `LOCAL_CONTROL_ROUTING` override is honored consistently across ONLINE/HYBRID/OFFLINE.
- Avoids duplicate twin checks in multiple mode branches.
- Keeps routing policy centralized in `_resolve_local_control_routing()`.
**Alternatives Considered**: Keep branch-local twin checks in OFFLINE/HYBRID only.

### Decision 2: Force-Twin Fallback Uses Local ACK, Not Cloud Forward
**Context**: On `force_twin`, non-`IsNewSet` polls can return no twin frame.
**Decision**: If twin dispatch returns no frame under `force_twin`, fall back to `_process_frame_offline(..., send_ack=True)` and continue.
**Rationale**:
- Preserves override intent (do not route local control poll frames to cloud).
- Maintains protocol-safe response behavior for poll requests.
- Keeps backward compatibility by only applying this strict fallback for explicit `force_twin`.
**Alternatives Considered**: Forward to cloud when twin has no payload.

---

## Task 16: Big-Bang Safety Switch + Startup Guards - Decisions

### Date: 2026-03-04

### Decision 1: Add Explicit Runtime Kill-Switch (`TWIN_KILL_SWITCH`)
**Context**: Production cutover needs emergency rollback that can disable twin route deterministically.
**Decision**: Add `TWIN_KILL_SWITCH` config flag and gate twin routing through `_is_twin_routing_available()`.
**Rationale**:
- Provides a single emergency switch that bypasses twin route selection.
- Keeps cloud/local fallback behavior intact.
- Avoids partial twin activation when rollback is requested.
**Alternatives Considered**: Re-activating legacy ACK path as default (rejected by scope guard).

### Decision 2: Block Invalid Safety Config at Startup
**Context**: Invalid twin config should not allow partial runtime startup.
**Decision**: Add `validate_startup_guards()` in config and call it from `main()` before `OIGProxy` startup.
**Rationale**:
- Fails fast with clear error before runtime side effects.
- Prevents unsafe combinations like `force_twin` with kill-switch enabled.
- Enforces deadline sanity for timeout behavior.
**Alternatives Considered**: Silent fallback to defaults for safety-critical combinations.

## 2026-03-04 04:17:25Z - F1 audit decision
- Mark cutover as conditionally compliant: core safety behavior is present and tested, but production readiness should be gated on legacy ACK branch removal and replacement of xfailed parity tests with green assertions.
