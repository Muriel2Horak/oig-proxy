## 2026-03-07

- Refactoring `_handle_box_connection` is safer when frame-processing, local-control routing, and mode routing are split into small async helpers with single-purpose guard clauses.
- Guard-return helper (`_process_box_frame_with_guard`) preserves connection resiliency behavior while flattening the main loop.
- Returning a `should_break` flag from routing helper keeps break/continue control flow explicit without increasing nesting.

- Refactoring transaction handlers in `digital_twin.py` is behavior-safe when each transition phase is isolated into helpers (match/validate -> state mutation -> result publication).
- Keeping all state mutation under the existing lock while extracting helpers preserves ordering guarantees and transaction invariants.

- Reducing deep nesting in `_on_ack_cloud_aligned` works best by splitting into: inflight match guard, conn validation, and isolated state mutation helper (`_apply_cloud_aligned_ack_state`) while preserving lock boundaries.
- Refactoring `on_tbl_event` into a locked processing helper (`_process_tbl_event_locked`) keeps async flow simple and avoids lock re-entry issues when auto-queuing follow-up SA commands outside the lock.
- Refactor pattern for deep async loop nesting in `proxy.py`: move per-iteration logic into a dedicated helper (`_handle_box_frame_iteration`) and keep main loop focused on read/break flow.
- Twin activation preflight is safer when isolated (`_activate_session_twin_mode_if_needed`) so the session loop starts flat and readable.
