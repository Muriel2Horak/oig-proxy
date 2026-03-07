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

- For ControlPipeline test repair, added a dedicated `tests/test_control_pipeline.py` aligned with the current slim API surface and kept `tests/test_control_methods.py` focused on `ControlSettings` behavior plus `append_to_log` coverage.
- Chose test-local dynamic module loading for `ControlPipeline` in both test files to keep LSP diagnostics clean without changing package layout or production import paths.

- For `tests/test_proxy_mqtt_setup.py` and `tests/test_proxy_mqtt_state.py`, removed/rewrote legacy assertions targeting deleted ControlPipeline MQTT orchestration methods and replaced them with assertions against current stable API behavior (state-cache setup idempotence, formatter/log/no-op helpers).
- For `tests/test_proxy_startup.py`, replaced removed `_ctrl` and `CONTROL_MQTT_PENDING_PATH` expectations with assertions based on current startup flow (`_msc.setup` invocation and `AUTO` device-id restoration path).

- For `tests/test_proxy_main_loop.py`, `tests/test_proxy_flow.py`, `tests/test_proxy_internal.py`, and `tests/test_proxy_additional.py`, migrated assertions from removed control/offline APIs to current entry points (`_respond_local_offline`, `_handle_frame_local_offline`, twin-backed status payloads, ControlPipeline no-op surface).
- Added `# pyright: reportMissingImports=false` to touched legacy test files that rely on runtime import path setup, so file-level diagnostics remain clean while preserving existing import style.

- Added a dedicated `tests/test_local_oig_crc.py` with direct unit coverage of every helper/branch in `addon/oig-proxy/local_oig_crc.py`, including cache behavior and encoding failure path, without modifying production CRC code.
- Chosen import strategy in this test: dynamic module load from file path (`spec_from_file_location`) to avoid dependence on hyphenated package path semantics and to keep diagnostics clean in static analysis.
- Added focused coverage module `tests/test_proxy_uncovered_paths.py` to isolate and exercise remaining defensive/error branches in `proxy.py` without modifying production code.
- Updated three pre-existing tests in control/proxy suites to match current `ControlSettings.handle_setting_event` behavior (publishes setting-event state through `_ctrl`).
