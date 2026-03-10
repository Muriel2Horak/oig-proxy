# Task 20 Pass Evidence: Runbook Completeness

**Date:** 2026-03-10
**Task:** T20 - Docs/runbook updates for new operating model

---

## Checklist

### README.md updates

| Requirement | Present | Location |
|-------------|---------|----------|
| Transport-only mode (`THIN_PASS_THROUGH`) documented | YES | "Klíčové funkce" bullet + "Tok komunikace" section + "Feature flags" table |
| Sidecar activation policy | YES | "Sidecar activation policy" section (line ~214) |
| Default transport mode documented | YES | Feature flags table: `THIN_PASS_THROUGH=false` |
| Sidecar activation threshold (3 fails) | YES | Threshold table: `HYBRID_FAIL_THRESHOLD` recommended `3` |
| 5-minute hysteresis | YES | "Deaktivační hystereze" section: "300 sekund (5 minut)" |
| Rollback order | YES | `LEGACY_FALLBACK=true → SIDECAR_ACTIVATION=false → THIN_PASS_THROUGH=false` |
| Configuration reference updated | YES | Expanded "Konfigurace env" section with feature flags table |

### docs/ops_twin_cutover_runbook.md updates

| Requirement | Present | Location |
|-------------|---------|----------|
| Activation/deactivation thresholds | YES | Part 6: Sidecar Activation Policy |
| `HYBRID_FAIL_THRESHOLD=3` for production | YES | Part 6.3 table |
| 300 s / 5-minute hysteresis window | YES | Part 6.2 deactivation thresholds |
| State machine diagram (ONLINE/OFFLINE transitions) | YES | Part 6.3 state machine |
| Rollback steps (immediate) | YES | Part 3.1: Immediate rollback (< 2 minutes) |
| Rollback verification | YES | Part 3.2: Rollback verification |
| Post-rollback procedure | YES | Part 3.3: Post-rollback |
| Canary deployment guide | YES | Part 7: Canary Deployment |
| Canary gate decision table | YES | Part 7.4 |
| Incident response section | YES | Part 8: Incident Response |
| Emergency transport-only mode procedure | YES | Part 8.5 |
| Emergency stop all writes procedure | YES | Part 8.4 |

### DEPLOYMENT.md updates

| Requirement | Present | Location |
|-------------|---------|----------|
| Feature flags section | YES | "Feature flags (nová architektura)" section |
| Sidecar activation policy reference | YES | Link to runbook Part 6 |
| Canary deployment guide | YES | "Canary nasazení" section |
| Rollback link to runbook | YES | Fáze 3 updated with runbook reference |
| Pre-flight checklist updated | YES | Added `HYBRID_FAIL_THRESHOLD=3` and `SIDECAR_ACTIVATION` items |

---

## Grep verification

```
README.md:
  - THIN_PASS_THROUGH: line 7, 52, 199
  - SIDECAR_ACTIVATION: lines 200, 214-246
  - HYBRID_FAIL_THRESHOLD: line 202, 245
  - 300 (seconds): line 222, 235, 246
  - rollback: line 195, 209

docs/ops_twin_cutover_runbook.md:
  - HYBRID_FAIL_THRESHOLD: line 546
  - 300 (hysteresis): line 526
  - rollback: lines 222-278
  - canary: lines 560-620
  - incident: lines 630-730
```

---

## Consistency check

- Default `THIN_PASS_THROUGH=false` documented in README, DEPLOYMENT.md, and runbook (Part 8.5 shows how to enable it, confirming `false` is default)
- `SIDECAR_ACTIVATION=false` as default in all three documents
- Hysteresis window: `300 s` stated consistently in README and runbook Part 6.2
- Failure threshold for OFFLINE: `3` (recommended production value) in README and runbook Part 6.3
- Rollback sequence `LEGACY_FALLBACK → SIDECAR_ACTIVATION → THIN_PASS_THROUGH` is consistent with task-2-flag-matrix.md

**Verdict: PASS**
