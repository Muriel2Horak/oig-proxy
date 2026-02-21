# Draft: Offline Proxy + Mock Alignment

## Requirements (confirmed)
- Prepare a concrete work plan to adjust offline proxy behavior and mock server behavior according to findings from online screening.
- Keep communication protocol-faithful to real cloud behavior observed during online mode.
- Validate behavior around IsNewFW/IsNewSet/IsNewWeather poll cycle and Setting handshake.
- Include hybrid-mode behavior assessment and avoid unintended fallback.

## Technical Decisions
- Planning output will be a single consolidated plan file.
- Focus on protocol/state-machine alignment, not isolated frame shape only.
- Keep rollback safety: do not remove backup path before verification milestones.

## Research Findings
- Real cloud can send Setting as response to IsNewFW (not IsNewSet only).
- In online mode, cloud-origin Setting was observed and BOX ACK with Reason=Setting was observed.
- Home-IP direct cloud probing produced resets; mobile-IP probing produced protocol replies (NACK/OneMore), indicating IP-dependent behavior.
- Mock replay with historical raw Setting still failed without matching session context.
- Hybrid mode may use per-frame local END on timeout without full offline switch.

## Scope Boundaries
- INCLUDE: Proxy offline/hybrid decision flow, mock server response/state behavior, logging/telemetry needed for diagnosis, validation/replay protocol.
- EXCLUDE: Unrelated MQTT discovery/entity refactors, broad UI/dashboard changes, production credential/network policy changes.

## Open Questions
- None blocking for plan generation; test strategy will default to existing repo test stack + agent-executed QA.
