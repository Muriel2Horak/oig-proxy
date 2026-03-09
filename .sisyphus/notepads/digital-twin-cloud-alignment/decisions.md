## Task: Twin inflight deterministic finalization (Blind Branch #2)

- Introduced `_finish_inflight_locked(...)` and routed `finish_inflight(...)` through it, so all terminal releases share the same INV-2 validation, timeout cancellation, and state clearing behavior.
- Marked `applied` as a terminal transition for queue-progress purposes by finalizing inflight directly in `_process_tbl_event_locked(...)` after building the applied result.
- Replaced ad-hoc `_inflight = None` / `_inflight_ctx = None` assignments in ACK-NACK and timeout terminal paths with `_finish_inflight_locked(...)` calls for deterministic cleanup.
