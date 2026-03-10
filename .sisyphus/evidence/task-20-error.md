# Task 20 Error Evidence: No Contradictions Found

**Date:** 2026-03-10
**Task:** T20 - Docs/runbook updates for new operating model

---

## Contradiction audit

Each documented claim was checked against the source evidence files.

### Claim: THIN_PASS_THROUGH default is `false`

- **Source:** task-2-flag-matrix.md line 21: `"Default Value": "false"`
- **README:** `THIN_PASS_THROUGH | false | Transport-only mode...`
- **Contradiction:** None.

### Claim: SIDECAR_ACTIVATION default is `false`

- **Source:** task-2-flag-matrix.md line 37: `"Default Value": "false"`
- **README:** `SIDECAR_ACTIVATION | false | ...`
- **Contradiction:** None.

### Claim: HYBRID_FAIL_THRESHOLD = 3 (recommended production)

- **Source:** task-3-cloud-fail-policy.md line 29: `"Threshold": 3 consecutive failures` and line 32: `HYBRID_FAIL_THRESHOLD (default: 1, recommended: 3)`
- **README:** `HYBRID_FAIL_THRESHOLD | 1 | ... Doporučeno: 3`
- **Runbook Part 6.3:** `HYBRID_FAIL_THRESHOLD | 1 | 3`
- **Contradiction:** None. Default `1` and recommended `3` match the policy spec.

### Claim: Sidecar deactivation hysteresis = 300 seconds (5 minutes)

- **Source:** task-4-hysteresis-policy.md line 14: `"Twin mode MUST remain active for at least 300 seconds (5 minutes)"`
- **README:** `300 sekund (5 minut)` in deactivation hysteresis section
- **Runbook Part 6.2:** `300-second (5-minute) hysteresis window`
- **Contradiction:** None.

### Claim: Anti-flap guard resets timer on any fail event

- **Source:** task-4-hysteresis-policy.md lines 19-25: `Any fail event during the hysteresis window MUST reset the deactivation timer`
- **README:** `Jakýkoli výpadek cloudu resetuje odpočet na nulu`
- **Runbook Part 6.2:** `Cloud ... failure | Timer resets to zero`
- **Contradiction:** None.

### Claim: Rollback order is LEGACY_FALLBACK → SIDECAR_ACTIVATION → THIN_PASS_THROUGH

- **Source:** task-2-flag-matrix.md lines 62-83: `LEGACY_FALLBACK → SIDECAR_ACTIVATION → THIN_PASS_THROUGH`
- **README:** `LEGACY_FALLBACK=true → SIDECAR_ACTIVATION=false → THIN_PASS_THROUGH=false`
- **DEPLOYMENT.md:** Same sequence in feature flags section
- **Contradiction:** None.

### Claim: Single-runtime policy not violated

- Docs do not suggest running multiple proxy processes simultaneously in production.
- Canary guide (DEPLOYMENT.md, runbook Part 7) uses a separate port for testing only, with the instruction to stop the original before promoting the canary.
- No command or suggestion implies two proxies serving the same BOX at the same time.
- **Contradiction:** None.

### Claim: No unsupported commands documented

- All `ha addons` commands used in the runbook match the Home Assistant Supervisor CLI syntax.
- `mosquitto_sub`, `mosquitto_pub`, `curl`, `docker` commands are standard tools available in the described environment.
- No commands reference non-existent scripts or flags.
- **Contradiction:** None.

---

## Single-runtime policy compliance

The docs document only one runtime (the add-on container). Transport-only mode and normal mode are mutually exclusive states of the same process, controlled by the `THIN_PASS_THROUGH` flag. The runbook canary section uses a separate container on a different port for pre-production testing only, never as a parallel production path.

**No single-runtime policy violations found.**

---

## Verdict: PASS (no contradictions detected)
