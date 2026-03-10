# Release Candidate Checklist - Task 25

**Generated:** 2026-03-10
**Plan:** proxy-thin-pass-through-twin-sidecar-refactor
**Wave:** 5 (Pre-final integration)

---

## Evidence Completeness Summary

| Task | Status | Evidence Files |
|------|--------|----------------|
| T1 | ✅ COMPLETE | task-1-baseline-map.md, task-1-baseline.json, task-1-missing-db-error.txt |
| T2 | ✅ COMPLETE | task-2-flag-matrix.md, task-2-flag-rollback.txt, +20 other files |
| T3 | ✅ COMPLETE | task-3-threshold-pass.txt, task-3-threshold-error.txt |
| T4 | ✅ COMPLETE | task-4-hysteresis-pass.txt, task-4-hysteresis-error.txt |
| T5 | ✅ COMPLETE | task-5-topic-pass.txt, task-5-topic-error.txt |
| T6 | ✅ COMPLETE | task-6-red.txt, task-6-groups.md |
| T7 | ✅ COMPLETE | task-7-pass.txt, task-7-error.txt |
| T8 | ✅ COMPLETE | task-8-pass.txt, task-8-error.txt |
| T9 | ✅ COMPLETE | task-9-pass.txt, task-9-error.txt |
| T10 | ✅ COMPLETE | task-10-pass.txt, task-10-error.txt |
| T11 | ✅ COMPLETE | task-11-pass.txt, task-11-error.txt |
| T12 | ✅ COMPLETE | task-12-pass.txt, task-12-error.txt |
| T13 | ✅ COMPLETE | task-13-pass.txt, task-13-error.txt |
| T14 | ✅ COMPLETE | task-14-pass.txt, task-14-error.txt |
| T15 | ✅ COMPLETE | task-15-pass.txt, task-15-error.txt |
| T16 | ✅ COMPLETE | task-16-pass.txt, task-16-error.txt |
| T17 | ✅ COMPLETE | task-17-pass.txt, task-17-error.txt |
| T18 | ⚠️ DIFFERENT FORMAT | task-18-validation-results.json (JSON format, not pass/error txt) |
| T19 | ❌ MISSING | NO evidence files found |
| T20 | ✅ COMPLETE | task-20-pass.md, task-20-error.md |
| T21 | ✅ COMPLETE | task-21-pass.txt, task-21-error.txt |
| T22 | ❌ MISSING | NO evidence files found |
| T23 | ✅ COMPLETE | task-23-pass.txt, task-23-error.txt |
| T24 | ✅ COMPLETE | task-24-pass.txt, task-24-error.txt |
| T25 | 🔄 CURRENT | Evidence to be generated |

---

## Missing Evidence Details

### Task 19 - Test suite simplification
**Status:** ❌ MISSING
**Expected evidence:**
- `.sisyphus/evidence/task-19-pass.txt`
- `.sisyphus/evidence/task-19-error.txt`

**Impact:** Cannot verify test suite simplification without this evidence.

### Task 22 - Full regression suite + coverage gate
**Status:** ❌ MISSING
**Expected evidence:**
- `.sisyphus/evidence/task-22-pass.txt`
- `.sisyphus/evidence/task-22-error.txt`

**Impact:** Cannot verify regression suite completion without this evidence.

### Task 18 - Alternative evidence format
**Status:** ⚠️ DIFFERENT FORMAT
**Existing evidence:**
- `task-18-validation-results.json` - Contains full validation results (PASS overall)
- `task-18-tool-validation-matrix.json` - Tool validation matrix
- `task-18-production-validation.md` - Production validation report

**Note:** Evidence exists but in JSON format instead of pass/error txt format. Overall status is PASS.

---

## Version Storage Check

- **Archive files found:** 1
  - `.sisyphus/evidence/f1-evidence.tar.gz` (F1 final verification evidence)
- **Version tags:** None found in evidence directory
- **Recommendation:** Consider creating versioned snapshots before final handoff

---

## Handoff Steps

### Pre-handoff Verification Commands

```bash
# 1. Run full test suite
python3 -m pytest tests/ -q

# 2. Run lint checks
python3 -m pylint addon/oig-proxy/proxy.py addon/oig-proxy/cloud_forwarder.py addon/oig-proxy/digital_twin.py

# 3. Verify feature flags
# Check THIN_PASS_THROUGH, SIDECAR_ACTIVATION, LEGACY_FALLBACK defaults

# 4. Verify telemetry contract
# Confirm tbl_* and tbl_events publishing unchanged
```

### Handoff Checklist

- [ ] All blocking tasks (T20, T21) completed
- [ ] T19 evidence generated (test suite simplification)
- [ ] T22 evidence generated (regression suite)
- [ ] Final verification wave (F1-F4) scheduled
- [ ] Runbook updated (T20 evidence exists)
- [ ] Canary rollout scripts ready (T21 evidence exists)

---

## Final Wave Readiness

**Blocked By:** T20, T21 (both ✅ COMPLETE)
**Blocks:** Final wave (F1-F4)

### Dependencies Met:
- ✅ T20: Docs/runbook updates - COMPLETE
- ✅ T21: Canary rollout and rollback scripts - COMPLETE

### Remaining Before Final Wave:
- ⚠️ T19: Test suite simplification evidence - NEEDS GENERATION
- ⚠️ T22: Regression suite evidence - NEEDS GENERATION

---

## Recommendations

1. **Generate missing evidence for T19 and T22 before final wave**
2. **Create versioned snapshot of evidence directory**
3. **Schedule F1-F4 final verification reviews**
4. **Confirm rollback procedures are documented and tested**

---

**RC Checklist Status:** ⚠️ PARTIALLY COMPLETE
**Next Action:** Generate evidence for T19 and T22, then proceed to Final Wave