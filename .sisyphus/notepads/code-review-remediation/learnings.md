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

- When `ControlPipeline` is reduced to formatter/coercion/no-op interfaces, replacement tests should validate stable outputs (`format_tx`, `format_result`, `coerce_value`) instead of legacy orchestration internals.
- For this repository layout (`addon/oig-proxy`), dynamic import via `importlib.util.spec_from_file_location` in tests avoids pyright missing-import noise while keeping runtime behavior unchanged.

- MQTT setup tests that invoke callbacks scheduling async coroutines should explicitly close created coroutines in test doubles to avoid `RuntimeWarning: coroutine was never awaited`.
- Startup tests should validate `_initialize_mqtt` + `_restore_device_id` behavior through `MqttStateCache.setup` and device-id restoration assertions, not legacy `_ctrl` hooks removed by refactor.

- Proxy tests touching offline behavior should target `_respond_local_offline` / `_handle_frame_local_offline` and `_handle_box_connection` routing, because legacy `_process_frame_offline` / `CloudForwarder.handle_frame_offline_mode` paths no longer exist.
- After refactor, status expectations should be based on twin-backed fields (`_twin._inflight`, `_twin._queue`) instead of removed `_ctrl` queue/inflight state.
- Slimmed `ControlPipeline` no longer owns MQTT ingest/start orchestration (`setup_mqtt`, `on_mqtt_message`, `start_inflight`); tests should validate available no-op and formatting methods instead of removed flow methods.

- For modules under `addon/oig-proxy` loaded outside normal import paths, test-local dynamic loading via `importlib.util.spec_from_file_location` provides deterministic runtime imports and keeps targeted coverage isolated to that file.
- CRC utility coverage can be made fully deterministic with a compact vector set: reflection edge width `0`, Modbus canonical payload (`123456789`), empty payload, framed/unframed CRC stripping, CRLF/no-CRLF output, UTF-8 payloads, and invalid surrogate encode failure.
- Achieving full coverage for `digital_twin.py` requires exercising internal guard branches and failure handlers directly (cloud availability checker exceptions, publisher failures, INV-3 timeout mismatch paths, and replay/disconnect helper paths).
- For `TwinMQTTHandler.setup_mqtt`, callback coverage is reliable by monkeypatching `asyncio.run_coroutine_threadsafe` and explicitly closing the scheduled coroutine in the test double to avoid un-awaited coroutine warnings.
- Coverage for cloud-aligned ACK paths is easiest when temporarily forcing `digital_twin.TWIN_CLOUD_ALIGNED` via monkeypatch to test both dispatcher routing and private helper behaviors in one test module.
- Reaching 100% coverage for `proxy.py` required explicit branch tests for twin routing gates, pending twin activation/session flags, ACK scheduling fallback, and local-control routing fallback paths.
- The final uncovered return in `_dispatch_local_control_via_twin` is practically unreachable with ordinary strings; a stateful equality test-double can deterministically force that defensive branch without production changes.
- Coverage command for this repository should target module import path (`--cov=proxy`) rather than file path (`--cov=addon/oig-proxy.proxy`) to avoid `module-not-imported` warnings.
