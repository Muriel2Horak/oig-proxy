# Changelog

## [1.4.8] - 2026-02-05

- Cache MQTT state payloads for telemetry top fields.
- Telemetry top: publish cached box fields (FW version, latency, last call, FW loaded time,
  WiFi strength, inverter model).
- Telemetry: `cloud_online` reflects successful cloud response or open session without errors
  in the window; `box_connected` reflects any connect/frame in the window.
- Telemetry logs: include logs for windows where the box never connected (to aid diagnostics).
- Cloud ACK timeout fixed at 1800s (no config override).

## [1.4.7] - 2026-02-05

- Internal refactor (superseded by 1.4.8 details).

## [1.4.6] - 2026-02-05

### Changed

- Internal refactor and telemetry tuning (superseded by 1.4.7 details).

## [1.4.5] - 2026-02-04

### Added

- Telemetry: `telemetry_stats` window aggregation for request/response counts.

### Changed

- Telemetry logs are sent only during a 2-window debug burst after WARNING/ERROR.
- Offline ACK: `IsNewSet` returns `END` with `Time/UTCTime`; `IsNewWeather` and
  `IsNewFW` return `END` without weather payloads.
- Telemetry: `box_connected` is true if the box connected or sent data at least
  once in the telemetry window.
- Telemetry: `cloud_online` is true if a cloud response arrived in the window or
  if the cloud session stayed open without errors/timeouts.
- Cloud ACK timeout is fixed at 1800s (no config override).

## [1.4.4] - 2026-02-03

### Changed

- Default `cloud_ack_timeout` increased to 1800s (30 minutes).

## [1.4.3] - 2026-02-03

### Fixed

- Proxy mode now respects explicit `online` configuration (no forced upgrade to `hybrid`).

## [1.4.2] - 2026-02-02

### Fixed

- Docker build: added missing telemetry_client.py to the image.

### Added

- Hybrid mode: immediate offline fallback after first cloud failure (introduced in upstream 1.4.1, first available in this Home Assistant add-on release).
- Telemetry: SQLite buffer for offline resilience (introduced in upstream 1.4.1, first available in this Home Assistant add-on release).

## [1.3.28] - 2026-01-03

### Changed

- Pylint config and code cleanup.
- Test helpers and utils updated for coverage runs.
- DNS helper and capture queue hooks aligned with tests.

## [1.3.27] - 2026-01-03

### Added

- Log sanitization for sensitive values (tokens/passwords).

### Changed

- Expanded unit tests and minor reliability improvements.
- Sonar/SonarCloud scripts and docs updated for analysis runs.
