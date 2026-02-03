# Changelog

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
