# Task 17 - Canary Go/No-Go Gate Report

## Decision

- Verdict: `LIMITED_GO`
- Reason: canary rollout ok, but legacy deletion blocked by dependency uncertainty

## Gate Scoring

| Gate | Status | Evidence | Deterministic rule | Notes |
|---|---|---|---|---|
| Parity | PASS | `.sisyphus/evidence/task-9-parity-tests.txt`, `.sisyphus/evidence/task-15-confidence-score.json` | tests pass AND overall_score>=0.80 | tests: pytest reports 7 passed; confidence: overall_score=0.8135 (threshold 0.8000) |
| Resilience | PASS | `.sisyphus/evidence/task-16-drift-report.json`, `.sisyphus/evidence/task-16-drift-anomaly-report.json` | severity==low AND confidence>=0.80 AND stable_days>=30 | drift_severity=low, confidence=0.82, stable_days=37 |
| Dependency | FAIL | `.sisyphus/evidence/task-2-topic-dependency-matrix.md` | no explicit risk markers in T2 matrix | T2 flags: Active compatibility risk, Dormant/contract drift risk, High uncertainty |
| Rollback | PASS | `.sisyphus/evidence/task-14-runbook-validation.txt`, `docs/ops_twin_cutover_runbook.md` | runbook validation ok AND rollback section present | runbook commands validated (T14) and rollback section present |

## Interpretation

- Canary rollout decision uses parity + resilience + rollback as safety gates; dependency gate controls whether legacy control can be deleted.
- Dependency gate intentionally strict for dead-code removal: any unknown external consumer risk blocks deletion.

## Legacy Control Status

- Decision: keep legacy control path available as a fallback; do not delete legacy code yet.
- Rationale: dependency audit shows uncertain external blast radius and/or compatibility risks.

## Remediation Backlog (Owners)

- [proxy-dev] Add explicit deprecation/compat layer: accept legacy `.../control/set` without being accidentally matched by wildcard, or document exact payload parity requirements.
- [ops] Inventory external HA automations/dashboards/Node-RED flows for subscriptions to `.../control/result` and `.../control/status/#`; capture evidence of none/usage.
- [docs] Publish migration guide: legacy topics -> Twin topics (`oig_local/<device_id>/<tbl>/<item>/set`, `oig_local/oig_proxy/twin_state/state`).
- [proxy-dev] Close T15 blind spots that affect canary confidence: cloud error event visibility + explicit mode transition telemetry.
- [release] Re-run this gate after dependency uncertainty is resolved; require Dependency gate PASS before deleting legacy code.
