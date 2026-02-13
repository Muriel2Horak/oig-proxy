# Changelog

## [1.6.0] - 2026-02-13

### Added

- CI: mypy type checking (blocking) in GitHub Actions
- CI: coverage threshold enforcement (`--cov-fail-under=80`)
- CI: hadolint Dockerfile linting
- CI: Bandit security scan is now blocking (removed `--exit-zero`)
- CI: unified Python 3.11 and actions/setup-python@v5 across all workflows

### Changed

- Dockerfile: replaced explicit COPY lines with `COPY *.py /app/` to
  automatically include all Python modules after refactoring.
- config.py: `CLOUD_ACK_TIMEOUT` now reads from environment variable
  with correct default of 1800.0s (was hardcoded to 30.0).
- run script: exports `CLOUD_ACK_TIMEOUT` from Home Assistant config.

### Removed

- Deleted `cloud_manager.py` (empty 27-line stub superseded by `cloud_forwarder.py`).
- Deleted `local_oig_crc.py` (90-line duplicate of `oig_frame.py`).
- Deleted corresponding test files `test_cloud_manager.py` and `test_local_oig_crc.py`.
- proxy.py: removed dead class constants (`_RESULT_ACK`, `_RESULT_END`,
  `_TIME_OFFSET`, `_POST_DRAIN_SA_KEY`), dead static method
  `_get_current_timestamp()`, dead wrapper methods
  `_handle_online_mode_connection()` / `_handle_offline_mode_connection()`,
  dead no-op `_cache_last_values()`, and unused imports.
- models.py: removed unused `WarningEntry` dataclass.
- telemetry_client.py: removed unused factory functions `init_telemetry()`
  and `get_telemetry_client()`.
- config.py: removed unused MQTT constants (`MQTT_CONNECT_TIMEOUT`,
  `MQTT_HEALTH_CHECK_INTERVAL`, `MQTT_PUBLISH_LOG_EVERY`).
- run script: removed cloud_queue environment variable exports.
- .coveragerc: removed reference to deleted `cloud_session.py`.
- utils.py: removed `cloud_queue` sensor configuration.
- Test files: removed skipped cloud_session placeholder tests, phantom
  attributes, and tests for deleted code.

## [1.5.3] - 2026-02-09

### Fixed

- Telemetry: avoid MQTT "session taken over" reconnect loops by stopping the old MQTT
  client before creating a new one and forcing a clean session.

## [1.5.2] - 2026-02-09

### Fixed

- Telemetry: do not permanently disable telemetry when `DEVICE_ID=AUTO` starts with an
  empty device id; telemetry begins sending once the device id is inferred.

## [1.5.1] - 2026-02-06

### Fixed

- HYBRID: only mark cloud success after a valid ACK; prevent fail counter reset on
  connect-only failures (e.g., immediate disconnect).

## [1.5.0] - 2026-02-06

### Added

- Telemetry: hybrid online/offline session tracking in window metrics (state, start/end,
  duration, reason).

### Changed

- HYBRID: attempt cloud once per retry interval even while offline; fallback
  to local ACK only after failed probe.
- HYBRID: retry interval default shortened to 60s.

## [1.4.9] - 2026-02-06

### Changed

- HYBRID: attempt cloud once per retry interval even while offline; fallback
  to local ACK only after failed probe.
- HYBRID: retry interval default shortened to 60s.

### Older

- Older entries have been trimmed for brevity.
