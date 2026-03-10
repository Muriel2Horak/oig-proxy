- 2026-03-10: Transport extraction is safest when `_route_box_frame_by_mode` delegates to a dedicated forward helper and keeps mode/twin logic outside that helper.
- 2026-03-10: Tests that instantiate `OIGProxy` via `__new__` need `getattr` defaults for new instance attrs to preserve backward compatibility.
- 2026-03-10: Gating hybrid offline fallback at CloudForwarder handler call sites (not inside `fallback_offline`) keeps OFFLINE/manual local ACK behavior untouched while making legacy fallback opt-in via flag in HYBRID failure paths.
- 2026-03-10: Moving setting-frame signature checks ahead of twin inflight lookup in `_maybe_handle_twin_ack` prevents unnecessary twin state queries during normal cloud-healthy traffic.
- 2026-03-10: Early thin-pass-through return in `_handle_box_frame_iteration` cleanly bypasses twin ACK/poll/deactivation branches and keeps transport-only behavior deterministic.
- 2026-03-10: Sidecar activation threshold behavior is safest when fail counting lives in `SidecarOrchestrator` state and cloud paths only emit failure/success events.
- 2026-03-10: Restricting sidecar fail counting to policy signals (`connect_failed`, `ack_timeout`) prevents non-policy cloud errors from triggering activation.
- 2026-03-10: Deactivation hysteresis is most stable when deactivation checks are event-driven through the sidecar orchestrator (`check_and_deactivate`) and require both idle twin state and elapsed stable-cloud window.
- 2026-03-10: Wrapping `_maybe_handle_twin_event` and `mqtt_publisher.publish_data` in `_process_box_frame_common` with logged fail-open guards protects transport from twin/telemetry dependency failures while preserving per-frame processing.

## Task 14: Control Routing Arbitration with Explicit Precedence

### Implementation Summary
- Added `resolve_route_target()` method to `ISidecarOrchestrator` interface
- Implemented in `SidecarOrchestrator` with clear precedence rules:
  1. force_cloud override (highest priority)
  2. force_twin override (only when twin activated)
  3. cloud_healthy=True => route to cloud (precedence rule)
  4. twin_activated=True => route to twin (fallback)
  5. Fallback to "local" (no viable route)

### Key Design Decisions
- Centralized arbitration in sidecar_orchestrator.py (single policy point)
- Proxy delegates via `ProxySidecarAdapter.resolve_route_target()`
- No dual-writer: single return point ensures only one target selected
- Clear logging: "ROUTING_ARBITRATION: route=X ..." for observability

### Precedence Rules Enforced
- Cloud healthy always wins over twin activation
- Sidecar only routes when explicitly activated
- Force overrides allow explicit control when needed
- Fallback chain: cloud -> twin -> local

### Files Modified
- addon/oig-proxy/sidecar_orchestrator.py: Added resolve_route_target() to interface and implementations
- addon/oig-proxy/proxy.py: Updated _resolve_local_control_routing() to delegate to sidecar

### Evidence Files Created
- .sisyphus/evidence/task-14-pass.txt: Cloud healthy => route to cloud
- .sisyphus/evidence/task-14-error.txt: Cloud fail + activation => route to twin
- 2026-03-10: Task 17 cleanup is safest when `_route_box_frame_by_mode` has a single default return to `_transport_only_forward(...)` and legacy OFFLINE/HYBRID fallbacks are the only guarded branches.
- 2026-03-10: For fault injection in `_handle_box_connection`, `cloud_writer is None` from `_cf.forward_frame` causes early loop break; use non-None writer sentinel in mocks when verifying multi-frame fail-open continuation under dependency failures.
- 2026-03-10: Performance testing should use `time.perf_counter()` for high-resolution timing and include warmup iterations to stabilize JIT/cache before measurement.
- 2026-03-10: When comparing baseline vs refactor latency, only positive deltas (actual regression) should be checked against tolerance; improvements (negative deltas) are acceptable.
- 2026-03-10: Simulated latency benchmarks should model realistic overhead: legacy path includes telemetry (0.5ms) and twin coupling (0.3ms), while thin transport has minimal overhead.
- 2026-03-10: P50/P95 tolerance thresholds should be documented and enforced: P50 ≤ 2.0ms regression, P95 ≤ 5.0ms regression for transport latency.
