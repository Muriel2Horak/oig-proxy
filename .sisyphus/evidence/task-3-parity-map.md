# Task 3 — Legacy control vs Twin parity map

## Scope
- Compared code-paths from incoming command to BOX write and result publish.
- Sources:
  - `addon/oig-proxy/digital_twin.py`
  - `addon/oig-proxy/twin_state.py`
  - `addon/oig-proxy/control_pipeline.py`
  - `addon/oig-proxy/control_settings.py`
  - `addon/oig-proxy/control_api.py`
  - `addon/oig-proxy/proxy.py`
  - `addon/oig-proxy/cloud_forwarder.py`
  - `tests/test_twin_*.py`
  - `tests/test_proxy_control_*.py`

## Lifecycle phases enumerated
- **accepted**
- **delivered**
- **acked**
- **applied**
- **error**

## End-to-end path mapping (incoming command → box write → result publish)

### Legacy path (current branch state)
1. Incoming command via HTTP:
   - `control_api._Handler.do_POST()` validates token + whitelist and calls `proxy._cs.send_setting(...)` (`control_api.py:74-149`).
2. ControlSettings queue entrypoint:
   - `ControlSettings.send_setting()` → `send_via_event_loop()` → `run_coroutine_threadsafe()` (`control_settings.py:116-234`).
   - Async target `ControlSettings.queue_setting()` currently delegates to `twin.queue_setting(...)` (`control_settings.py:235-273`).
3. Legacy state machine file:
   - `ControlPipeline` lifecycle handlers are placeholders/no-op (`control_pipeline.py:177-214`, `252-263`), no active tx state transitions.
4. BOX write on legacy/cloud route:
   - BOX poll frame forwarded to cloud in `CloudForwarder.forward_frame()` and ACK/END/NACK written back via `forward_ack_to_box()` (`cloud_forwarder.py:466-523`, `421-460`).
5. Result publish:
   - tbl_events setting publish path goes through `ControlSettings.handle_setting_event()` → `_ctrl.publish_setting_event_state(...)` (`control_settings.py:61-95`), which publishes MQTT state in `ControlPipeline.publish_setting_event_state()` (`control_pipeline.py:216-250`).

### Twin path
1. Incoming command via HTTP or MQTT:
   - HTTP: `ControlSettings.queue_setting()` builds `QueueSettingDTO` then `DigitalTwin.queue_setting()` (`control_settings.py:235-273`, `digital_twin.py:389-417`).
   - MQTT: `TwinMQTTHandler._default_on_mqtt_message()` parses topic/payload and calls `DigitalTwin.queue_setting()` (`digital_twin.py:151-208`).
2. BOX write (poll-driven):
   - `proxy._maybe_handle_local_control_poll()` routes to twin and calls `_dispatch_local_control_via_twin()` (`proxy.py:641-664`, `967-1029`).
   - Twin delivers setting only on `IsNewSet` through `on_poll()` / `_deliver_on_is_new_set()` / `_build_delivery_response()` (`digital_twin.py:1065-1217`).
3. ACK correlation:
   - ACK/NACK/END parsed in `proxy._maybe_handle_twin_ack()` and converted to `OnAckDTO` (`proxy.py:1030-1093`).
   - Twin ACK handling in `DigitalTwin.on_ack()` with cloud-aligned or legacy mode branch (`digital_twin.py:595-856`).
4. Applied detection:
   - `proxy._maybe_handle_twin_event()` parses tbl_events and sends `OnTblEventDTO` (`proxy.py:1094-1143`).
   - `DigitalTwin.on_tbl_event()` marks applied and optionally auto-queues SA (`digital_twin.py:872-965`).
5. Result publish:
   - Twin publishes state snapshots via `_publish_state()` to `.../twin_state/state` (`digital_twin.py:363-384`).

## Feature-by-feature parity table

| Feature / Phase | Legacy control path | Twin path | Parity | Gap tag | Exact references |
|---|---|---|---|---|---|
| accepted | API accepts via whitelist + `send_setting`; returns queued/ok | `queue_setting` returns `TransactionResultDTO(status="accepted")` | **Partial** | acceptable-difference | `control_api.py:120-149`, `control_settings.py:116-173`, `digital_twin.py:389-417`, `tests/test_twin_cloud_parity.py:110-145` |
| delivered | No active `ControlPipeline` delivery logic (stub) | Poll-driven delivery only on `IsNewSet`; writes Setting frame to BOX | **No** | must-fix | `control_pipeline.py:208-214`, `proxy.py:967-1009`, `digital_twin.py:1097-1217`, `tests/test_twin_poll_delivery.py:62-109` |
| acked | Legacy cloud route forwards cloud ACK bytes to BOX; no active pipeline correlation state | Twin correlates ACK/NACK/END to inflight tx and conn_id | **Partial** | acceptable-difference | `cloud_forwarder.py:421-460`, `control_pipeline.py:252-263`, `proxy.py:1030-1093`, `digital_twin.py:595-856`, `tests/test_twin_ack_correlation.py:71-208` |
| applied | Legacy applied publication from `tbl_events` through `ControlSettings.handle_setting_event` | Twin `on_tbl_event` moves to APPLIED and publishes twin state | **Partial** | acceptable-difference | `control_settings.py:61-95`, `control_pipeline.py:216-250`, `proxy.py:1094-1143`, `digital_twin.py:872-947` |
| error | Legacy cloud handles timeout/eof/error with fallback and telemetry | Twin handles NACK, disconnect, timeout, replay-max-exceeded | **Partial** | acceptable-difference | `cloud_forwarder.py:340-360`, `525-553`; `digital_twin.py:749-771`, `966-1060`, `1460-1515`, `tests/test_twin_replay_resilience.py:405-431` |
| normalization | `ControlPipeline.normalize_value()` exists but not wired to send path | Twin queues raw `new_value` (string) from HTTP/MQTT | **No** | must-fix | `control_pipeline.py:111-145`, `control_settings.py:235-273`, `digital_twin.py:180-186` |
| whitelist | Enforced in HTTP API (`CONTROL_WRITE_WHITELIST`) | Twin MQTT command path accepts topic/payload without whitelist check | **No** | must-fix | `control_api.py:120-139`, `config.py:170-177`, `digital_twin.py:151-208` |
| tx lifecycle model | `ControlPipeline` tx lifecycle methods are no-op placeholders | Twin has explicit queue/inflight/stage transitions + DTOs | **No** | must-fix | `control_pipeline.py:177-214`, `252-263`; `twin_state.py:30-39`, `203-447`; `digital_twin.py:389-586`, `872-947` |
| retries / replay | Cloud path retries by mode fallback/reconnect (cloud-level), not per-setting tx replay state | Twin has disconnect replay buffer + replay count, but no active per-tx retry loop using `max_attempts/retry_delay_s` | **Partial** | must-fix | `cloud_forwarder.py:466-553`, `hybrid_mode.py:48-101`; `digital_twin.py:80-86`, `966-1444`, `1460-1515`, `tests/test_twin_replay_resilience.py:289-364` |
| timeout semantics | Cloud timeout = waiting for cloud ACK (`CLOUD_ACK_TIMEOUT`) | Twin timeout = ack/applied stage deadlines (`ack_timeout_s`, `applied_timeout_s`) | **No** | acceptable-difference | `cloud_forwarder.py:380-416`, `525-534`; `digital_twin.py:1460-1515`, `tests/test_twin_cloud_parity.py:347-440` |

## Notes on expected-behavior coverage
- Twin behavior coverage is strong for delivery/ACK/replay/timeouts in:
  - `tests/test_twin_poll_delivery.py`
  - `tests/test_twin_ack_correlation.py`
  - `tests/test_twin_cloud_parity.py`
  - `tests/test_twin_replay_resilience.py`
  - `tests/test_twin_e2e_roundtrip.py`
- Legacy `test_proxy_control_*.py` coverage validates mostly formatting/no-op safety, consistent with `ControlPipeline` stubbed lifecycle.

## Non-parity summary (all tagged)
1. Missing active legacy tx lifecycle machine in `ControlPipeline` vs Twin explicit stages — **must-fix**.
2. Missing normalization in active send path (legacy helper unused, Twin raw) — **must-fix**.
3. Missing whitelist on Twin MQTT set ingestion path — **must-fix**.
4. Retry model mismatch (cloud connectivity retry vs Twin tx replay; no active per-tx retry loop) — **must-fix**.
5. Timeout semantics differ by design (cloud ACK timeout vs Twin stage timeout) — **acceptable-difference**.
6. ACK mismatch handling differs by mode (`TWIN_CLOUD_ALIGNED` returns None vs legacy invariant exception) — **acceptable-difference**.
