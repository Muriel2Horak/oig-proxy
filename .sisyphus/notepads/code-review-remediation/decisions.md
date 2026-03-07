## 2026-03-07

- Added three internal helpers instead of changing `_handle_box_connection` signature or external behavior:
  - `_process_box_frame_with_guard`
  - `_maybe_handle_local_control_poll`
  - `_route_box_frame_by_mode`
- Preserved original logging and disconnect reason assignment exactly at cloud-failure path (`cloud_failure`) while moving logic into helper.
- Kept top-level exception handling in `_handle_box_connection` unchanged, as required by inherited error-handling policy.

- Split long methods into private helpers without changing public signatures for `queue_setting`, `_on_ack_cloud_aligned`, `on_tbl_event`, `on_disconnect`, and `on_reconnect`.
- Preserved all original logging strings and state transitions; helper boundaries follow transaction lifecycle phases only (no semantic changes).

- For `_on_ack_cloud_aligned`, extracted `_get_matching_inflight_for_ack` and `_apply_cloud_aligned_ack_state` to flatten branching while retaining identical ACK/NACK state updates and timeout scheduling.
- For `on_tbl_event`, extracted `_process_tbl_event_locked` to keep all inflight/state updates under the lock and perform optional SA auto-queue outside lock exactly as before.
- Chosen refactor approach: preserve `_handle_box_connection` behavior and exception handling, only extract structural helpers (`_activate_session_twin_mode_if_needed`, `_handle_box_frame_iteration`) and keep routing/ACK semantics in existing methods.
