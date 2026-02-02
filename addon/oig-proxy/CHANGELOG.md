# Changelog

## [1.4.2] - 2026-02-02

### Fixed

- Docker build: added missing telemetry_client.py to the image.
- Hybrid mode: immediate offline fallback after first cloud failure.
- Telemetry: SQLite buffer for offline resilience.

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
