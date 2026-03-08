# Task 3 — Gap severity classification

## Rubric used
- **must-fix**: can change write safety, command admissibility, or transaction correctness.
- **acceptable-difference**: implementation divergence with bounded/known behavior and no immediate safety break.

## Classified gaps

| Gap ID | Gap | Severity tag | Why | Suggested follow-up task mapping |
|---|---|---|---|---|
| G1 | Legacy `ControlPipeline` has no active tx lifecycle while Twin has explicit stage machine | must-fix | Missing lifecycle parity prevents consistent semantics across routing paths | T4-lifecycle-parity (proposed) |
| G2 | Normalization helper exists (`ControlPipeline.normalize_value`) but is not in active command path; Twin also accepts raw values | must-fix | Can write non-canonical values and drift from expected BOX value format | T5-normalization-wiring (proposed) |
| G3 | HTTP path is whitelist-gated but Twin MQTT set path has no whitelist enforcement | must-fix | Command admissibility differs by ingress path; bypass risk on MQTT path | T6-whitelist-unification (proposed) |
| G4 | Retry model mismatch: cloud connectivity retry/fallback vs Twin replay buffer; no active per-setting retry loop from `max_attempts/retry_delay_s` | must-fix | Recovery guarantees differ and can produce divergent delivery behavior | T7-retry-timeout-alignment (proposed) |
| G5 | Timeout semantics differ (cloud ACK wait timeout vs Twin stage timeout handlers) | acceptable-difference | Different architecture layers; behavior can still be valid if documented and monitored | T8-timeout-contract-doc (proposed) |
| G6 | ACK wrong-connection behavior differs by Twin mode (`TWIN_CLOUD_ALIGNED`: ignore/None, legacy: invariant error) | acceptable-difference | Explicit mode contract already covered by tests; divergence is intentional/config-driven | T9-mode-contract-hardening (proposed) |

## Critical-gap mapping check
- Critical (must-fix) gaps identified: **G1, G2, G3, G4**.
- All critical gaps have explicit follow-up mapping in table above.
- Classification coverage: **6/6 gaps classified**.
