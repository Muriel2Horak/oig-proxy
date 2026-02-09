# Changelog

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
