# Local GetActual Option Specification

Status: Accepted for planning.
Date: 2026-06-27
Base branch: main
Current released add-on version: 2.1.0
Target test version: 2.1.1

## Problem

Some OIG boxes/FW combinations appear to stay connected through the proxy, but fresh `tbl_actual` data is only observed roughly every 5 minutes. Telemetry comparison showed that affected boxes can be online while not receiving cloud `ACK GetActual` responses, whereas other boxes do receive those cloud responses.

The proxy should offer an explicit opt-in workaround that injects `GetActual` frames from the proxy to the box. This lets each installation enable the behavior only when needed, without changing the default behavior for working boxes.

## Goals

- Add Home Assistant add-on configuration options for local/proxy-injected `GetActual`.
- Keep the feature disabled by default.
- When enabled, send `ACK + ToDo=GetActual` to the connected box immediately after a session becomes active, then periodically.
- Make injected frames observable via existing frame capture and telemetry direction counters.
- Allow enable/disable testing on our HA instance using `deploy_to_haos.sh`.
- Keep the implementation small and compatible with the v2.1.0 modular proxy architecture.

## Non-Goals

- Do not change `proxy_mode` semantics.
- Do not make this a cloud replacement or a setting-control mechanism.
- Do not enable the feature globally by default.
- Do not add live runtime toggling without add-on restart in this iteration.
- Do not change sensor parsing, MQTT discovery, or digital twin setting delivery.

## Proposed Configuration

Add two HA add-on options:

```yaml
local_getactual_enabled: false
local_getactual_interval_s: 10
```

Environment variables exported by `run`:

```bash
LOCAL_GETACTUAL_ENABLED=false
LOCAL_GETACTUAL_INTERVAL_S=10
```

Python config fields:

```python
local_getactual_enabled: bool = False
local_getactual_interval_s: int = 10
```

Values below 10 seconds must be clamped to 10 seconds in Python to avoid accidental frame flooding. The default is 10 seconds because that matches the expected standard fresh-data cadence.

## Runtime Behavior

When `local_getactual_enabled` is false:

- No task is started.
- No frames are injected.
- Existing online/hybrid/offline behavior remains unchanged.

When `local_getactual_enabled` is true:

- For each active box TCP session, start at most one local GetActual task.
- Send `build_getactual_frame()` to the box writer immediately.
- Repeat after `local_getactual_interval_s`.
- Stop the task when the box session ends or the writer errors.
- Do not cancel the main proxy pipes if the local GetActual task fails.
- Capture the injected frame with direction `proxy_to_box`.
- Increment telemetry frame direction `proxy_to_box`.
- Log a concise debug message for each injected frame.

## Expected Files

- `addon/oig-proxy/config.json`
  - Add options/schema entries.
  - Bump add-on version from `2.1.0` to `2.1.1` for test deployment.
- `addon/oig-proxy/run`
  - Read the new options with safe defaults.
  - Export `LOCAL_GETACTUAL_ENABLED` and `LOCAL_GETACTUAL_INTERVAL_S`.
- `addon/oig-proxy/config.py`
  - Parse the new env vars into `Config`.
  - Keep defaults false/30.
- `addon/oig-proxy/proxy/server.py`
  - Import `build_getactual_frame`.
  - Add cancellable local GetActual loop.
  - Start/cancel it per active box session without affecting main forwarding.
- `docs/v2/configuration.md`
  - Document the new options and restart requirement.
- `tests/v2/test_addon_dns_config.py`
  - Add config/env bridge coverage for defaults and run script exports.
- `tests/v2/test_proxy/test_server.py`
  - Add async unit tests for enabled/disabled local GetActual behavior.

## Acceptance Criteria

- Default install after upgrade behaves exactly as before because `local_getactual_enabled` defaults to `false`.
- A user can enable the feature in HA add-on configuration with `local_getactual_enabled: true`.
- A user can configure the interval with `local_getactual_interval_s`, default `10`.
- On an enabled box session, the proxy sends a `GetActual` frame immediately and then periodically.
- On disabled configuration, no proxy-injected `GetActual` frame is sent.
- Injected frames are visible in capture as `direction=proxy_to_box` and contain `<ToDo>GetActual</ToDo>`.
- The local GetActual task is cancelled when the box disconnects and does not leak tasks.
- Unit tests cover config defaults, env override, run export, disabled behavior, enabled behavior, and cancellation/error handling.
- Focused tests pass locally before HA deployment.
- `./deploy_to_haos.sh` deploys to our HA instance.
- On our HA box, enabling the option increases fresh `tbl_actual` cadence versus the 5-minute baseline.
- Disabling the option stops proxy-injected `GetActual` frames after add-on restart.

## HA Test Procedure

1. Build from `main` on a feature branch.
2. Run focused unit tests locally.
3. Deploy to our HA using:

   ```bash
   ./deploy_to_haos.sh
   ```

4. Verify add-on starts and logs show version `2.1.1`.
5. Baseline with `local_getactual_enabled: false` for at least 10 minutes.
6. Enable:

   ```yaml
   local_getactual_enabled: true
   local_getactual_interval_s: 10
   ```

7. Restart add-on and observe for at least 20 minutes.
8. Confirm `tbl_actual` cadence improves and `proxy_to_box` GetActual frames appear in capture/telemetry.
9. Disable the option, restart add-on, and confirm proxy-injected frames stop.

## Decisions

- Default interval is `10` seconds.
- Minimum interval is `10` seconds.
- Enable/disable is intentionally applied through HA add-on configuration and add-on restart. Live toggling is deferred.
