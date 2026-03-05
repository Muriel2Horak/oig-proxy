# Handoff Checklist: Follow-Up Sprint Execution

**Document Version:** 1.0
**Generated:** 2026-02-19
**Based On:** task-17-data-adjustment-backlog.md, task-15-blind-spot-quantification.json
**Blocking Tasks:** F1-F4 (future work)

---

## Executive Summary

This handoff provides a structured execution plan for the follow-up sprint, focusing on:
- **10 safe improvement items** (from Task 17 backlog)
- **9 identified blind spots** (from Task 15 quantification)
- **7 high/medium priority recommendations** for instrumentation

All items are **safe-only, no-breaking-changes** with clear acceptance criteria.

---

## 1. Prerequisites

### 1.1 Documentation Readiness

**BLOCKS ALL EXECUTION**

- [ ] Read and understand `.sisyphus/evidence/task-15-blind-spot-quantification.json`
  - **Why:** Identifies 9 blind spots affecting confidence scores (overall: 81.35%)
  - **Key Focus:** 2 high-severity blind spots requiring immediate attention
  - **Verification:** Reviewer quiz on blind spot IDs and severity levels

- [ ] Read and understand `.sisyphus/evidence/task-17-data-adjustment-backlog.md`
  - **Why:** Contains 10 safe, prioritized improvement items
  - **Key Focus:** 3 high-priority items (DA-001, DA-002, DA-004)
  - **Verification:** Acceptance criteria review for each backlog item

### 1.2 Environment Readiness

**BLOCKS EXECUTION OF SPECIFIC PHASES**

- [ ] Development environment configured
  - Python 3.11+ installed
  - SQLite available for frame DB testing
  - MQTT broker (Mosquitto) running for telemetry validation
  - **Verification:** `python --version`, `sqlite3 --version`, `mosquitto --version`

- [ ] Test capture dataset available
  - Minimum 24 hours of passive capture data
  - Located in `/data/payloads.db` or equivalent
  - **Verification:** `sqlite3 /data/payloads.db "SELECT COUNT(*) FROM frames; SELECT MAX(timestamp) - MIN(timestamp) FROM frames;"`

- [ ] Git branch created for follow-up sprint
  - Branch name: `follow-up-sprint-[date]`
  - Clean working directory
  - **Verification:** `git status` shows clean, `git branch --show-current` matches

### 1.3 Codebase Readiness

- [ ] Base branch is stable
  - Last known good commit: `<commit-hash>` (update after sprint approval)
  - All tests passing on base branch
  - **Verification:** `./.github/scripts/run_tests.sh` passes

- [ ] CloudForwarder, StateManager, FrameParser files identified
  - Files to modify:
    - `addon/oig-proxy/cloud_forwarder.py` (for DA-001, DA-002, high-priority recommendations)
    - `addon/oig-proxy/state_manager.py` (for mode transition telemetry)
    - `addon/oig-proxy/frame_parser.py` (for NACK reason parsing)
  - **Verification:** All files exist and are readable

---

## 2. Execution Gates

### Phase 1: Telemetry Instrumentation (Priority: HIGH)

**Gate Condition:**
```
PASS IF:
- All DA-001, DA-002, DA-004 acceptance criteria met
- Telemetry metrics visible in MQTT discovery
- No regression in existing telemetry

FAIL IF:
- New metrics not published to MQTT
- Existing metrics broken or renamed
- Frame capture rate drops > 5% during implementation
```

**Gate 1.1: NACK Reason Tracking (DA-001)**
- [ ] NACK frame parser captures 'Reason' field value
- [ ] Telemetry metric 'nack_reasons' published with breakdown
- [ ] Historical NACK analysis possible via telemetry snapshot
- [ ] **Evidence Reference:** task-11-edge-cases.json - 27 NACK events detected
- [ ] **Effort Estimate:** 2 hours

**Gate 1.2: Cloud Gap Duration Histogram (DA-002)**
- [ ] Telemetry publishes 'cloud_gap_duration_ms' histogram
- [ ] Buckets configured: <60s, 60-120s, 120-300s, 300-600s, >600s
- [ ] Visible in telemetry snapshot output
- [ ] **Evidence Reference:** task-10-mode-cloud-transitions.json - 66 cloud gaps (301-381s range)
- [ ] **Effort Estimate:** 2 hours

**Gate 1.3: Pairing Confidence Telemetry (DA-004)**
- [ ] Telemetry publishes 'pairing_confidence' gauge
- [ ] Counters for high/medium/low confidence pairs
- [ ] Percentages updated in proxy_status telemetry
- [ ] **Evidence Reference:** task-9-signal-timeline.json - 31 low confidence pairs (8.5%)
- [ ] **Effort Estimate:** 3 hours

**Phase 1 Rollback:**
If any gate fails:
```bash
# Revert telemetry changes
git revert <telemetry-commit-hash>
# Verify existing telemetry restored
# Re-run tests
./.github/scripts/run_tests.sh
```

---

### Phase 2: Blind Spot Remediation (Priority: HIGH)

**Gate Condition:**
```
PASS IF:
- cloud_error_events_invisible blind spot filled (exception logging added)
- mode_transitions_mostly_inferred blind spot filled (direct telemetry)
- Confidence scores improve in quantification script

FAIL IF:
- Cloud errors still invisible in frame DB
- Mode transitions still inferred (not direct)
- No measurable improvement in confidence scores
```

**Gate 2.1: Cloud Error Exception Logging**
- [ ] Structured exception logging added to `cloud_forwarder.py`
- [ ] Cloud timeout events written to DB or error_events table
- [ ] Cloud EOF events captured
- [ ] Cloud generic errors captured
- [ ] **Fills Blind Spot:** cloud_error_events_invisible (severity: HIGH)
- [ ] **Expected Confidence Gain:** edge_case_detection +0.30 (from 0.64 to 0.94)
- [ ] **Effort Estimate:** 4 hours

**Gate 2.2: Explicit Mode-Change Telemetry**
- [ ] StateManager emits explicit ONLINE/OFFLINE/HYBRID/REPLAY events
- [ ] Events published as MQTT telemetry
- [ ] Direct transition rate >= 10% (from current 0.05%)
- [ ] **Fills Blind Spot:** mode_transitions_mostly_inferred (severity: HIGH)
- [ ] **Expected Confidence Gain:** mode_transition_inference +0.15 if 50% become direct
- [ ] **Effort Estimate:** 3 hours

**Gate 2.3: Re-run Blind Spot Quantification**
- [ ] Execute `scripts/protocol_analysis/quantify_blind_spots.py`
- [ ] Verify overall confidence >= 0.85 (from 0.8135)
- [ ] Verify edge_case_detection >= 0.90 (from 0.64)
- [ ] Verify mode_transition_inference >= 0.80 (from 0.70)
- [ ] **Evidence:** Updated task-15-blind-spot-quantification.json with new scores

**Phase 2 Rollback:**
```bash
# Revert instrumentation changes
git revert <instrumentation-commit-hash>
# Re-run quantification to verify baseline restored
python scripts/protocol_analysis/quantify_blind_spots.py
# Check confidence scores return to baseline (0.8135)
```

---

### Phase 3: Documentation Updates (Priority: MEDIUM)

**Gate Condition:**
```
PASS IF:
- All 3 documentation items completed
- Docs reviewed for technical accuracy
- README.md updated with new telemetry metrics

FAIL IF:
- Documentation contains factual errors
- Missing references to evidence files
- README.md not updated
```

**Gate 3.1: Cloud Response Ratio Documentation (DA-003)**
- [ ] New section in `docs/protocol_analysis/cloud_reliability.md`
- [ ] Explains observed response ratio range (0.63-0.982)
- [ ] Provides monitoring guidance and alert thresholds
- [ ] **Evidence Reference:** task-10-mode-cloud-transitions.json
- [ ] **Effort Estimate:** 1 hour

**Gate 3.2: Signal Timing Documentation (DA-006)**
- [ ] New `docs/protocol_analysis/signal_timing.md`
- [ ] Tables for each signal class (min/max/avg/std_dev)
- [ ] Observed vs configured tolerance comparison
- [ ] **Evidence Reference:** task-2-contract-matrix.json
- [ ] **Effort Estimate:** 2 hours

**Gate 3.3: Connection Lifecycle Documentation (DA-010)**
- [ ] New `docs/protocol_analysis/connection_lifecycle.md`
- [ ] State diagram of transitions
- [ ] Statistics on transition frequencies (18,598 total)
- [ ] Recovery pattern documentation
- [ ] **Evidence Reference:** task-10-mode-cloud-transitions.json
- [ ] **Effort Estimate:** 3 hours

**Gate 3.4: README.md Update**
- [ ] New telemetry metrics documented in README.md
- [ ] Configuration options for new features listed
- [ ] Links to new documentation sections added
- [ ] **Effort Estimate:** 1 hour

---

### Phase 4: Optional Enhancements (Priority: LOW-MEDIUM)

**Gate Condition:**
```
PASS IF:
- At least 3 of 5 items completed
- All completed items pass tests
- No regression in existing functionality

FAIL IF:
- Completed items introduce breaking changes
- Items fail acceptance criteria
```

**Gate 4.1: Frame Direction Counters (DA-005)**
- [ ] Three telemetry counters implemented
- [ ] Counters included in daily telemetry snapshot
- [ ] Ratios calculated and published
- [ ] **Evidence Reference:** task-9-signal-timeline.json distribution
- [ ] **Effort Estimate:** 1 hour

**Gate 4.2: Signal Class Distribution (DA-007)**
- [ ] Separate counters for each signal class
- [ ] Updated in real-time as frames processed
- [ ] Included in proxy_status telemetry window
- [ ] **Effort Estimate:** 2 hours

**Gate 4.3: Cloud Response Ratio Threshold Config (DA-008)**
- [ ] New config option 'cloud_response_ratio_min_threshold' (default: 0.7)
- [ ] Warning logged if ratio falls below threshold
- [ ] Config documented in README.md
- [ ] **Effort Estimate:** 2 hours

**Gate 4.4: END Frame Frequency (DA-009)**
- [ ] Counter 'end_frames_received' implemented
- [ ] Counter 'end_frames_sent' implemented
- [ ] Time-since-last-END metric published
- [ ] **Evidence Reference:** task-11-edge-cases.json - 18,334 disconnect events
- [ ] **Effort Estimate:** 1 hour

**Gate 4.5: Per-Direction Timestamps (Medium Priority Recommendation)**
- [ ] Add t_box_recv, t_cloud_send, t_cloud_recv, t_proxy_send to frame DB
- [ ] Segments visible in query output
- [ ] **Fills Blind Spot:** no_direct_cloud_to_box_latency
- [ ] **Expected Confidence Gain:** timing_fidelity +0.08 (from 0.70 to 0.78)
- [ ] **Effort Estimate:** 6 hours

---

## 3. Success Criteria

### 3.1 Quantitative Metrics

| Metric | Target | Baseline | Measurement Method |
|--------|--------|----------|-------------------|
| Overall Confidence Score | >= 0.85 | 0.8135 | `quantify_blind_spots.py` |
| Edge Case Detection Score | >= 0.90 | 0.64 | `quantify_blind_spots.py` |
| Mode Transition Inference | >= 0.80 | 0.70 | `quantify_blind_spots.py` |
| High-Priority Items Complete | 100% (3/3) | 0% | Checklist review |
| Medium-Priority Items Complete | >= 75% (6/8) | 0% | Checklist review |
| Test Coverage | >= 80% | Current | `run_tests.sh` |
| Frame Capture Rate | <= 5% drop | Current | DB query pre/post |

### 3.2 Qualitative Criteria

- [ ] All acceptance criteria for completed items met
- [ ] No regression in existing telemetry metrics
- [ ] Documentation reviewed and approved by technical reviewer
- [ ] Code passes all automated quality gates (Sonar, Bandit)
- [ ] No breaking changes introduced
- [ ] Rollback procedures tested and documented

### 3.3 Deliverable Checklist

- [ ] Code changes committed and pushed to follow-up branch
- [ ] All new documentation files in `docs/protocol_analysis/`
- [ ] README.md updated with new features
- [ ] Updated blind spot quantification report (post-implementation)
- [ ] Telemetry validation report (metrics visible in MQTT)
- [ ] Test results saved (coverage >= 80%, all tests passing)
- [ ] Rollback documentation complete

---

## 4. Risks and Mitigations

### 4.1 Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Exception logging adds performance overhead** | Medium | Medium | Profile before/after; use async logging; batch DB writes |
| **Mode-change telemetry increases MQTT traffic** | Low | Low | Debounce rapid mode switches; use counter aggregation |
| **Per-direction timestamps require schema migration** | High | Low | Create migration script; test on backup DB first |
| **NACK reason parsing encounters unknown codes** | Low | Low | Fallback to 'unknown' bucket; alert on new reason codes |

### 4.2 Operational Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Test dataset insufficient for validation** | Medium | Medium | Capture 24+ hours of data before sprint; verify edge cases present |
| **Documentation drifts from implementation** | Medium | Low | Technical review gates; doc-as-code approach |
| **Rollback procedures fail during production** | Low | High | Test rollback on staging first; document exact steps |
| **Team availability changes mid-sprint** | Low | Medium | All items have effort estimates; can pause at gate boundaries |

### 4.3 Blind Spot-Specific Risks

**Cloud Error Visibility (HIGH Priority)**
- **Risk:** Adding exception logging may expose sensitive error messages
- **Mitigation:** Sanitize error messages before logging; audit log output

**Mode Transition Telemetry (HIGH Priority)**
- **Risk:** Direct telemetry may not align with heuristics (confusion in analysis)
- **Mitigation:** Run both systems in parallel for 48 hours; compare results before retiring heuristics

**Per-Direction Timestamps (MEDIUM Priority)**
- **Risk:** Clock skew between proxy and cloud may corrupt measurements
- **Mitigation:** Use monotonic clocks; document clock sync requirements

---

## 5. Dependencies

### 5.1 Internal Dependencies

| Dependency | Required By | Status | Notes |
|------------|-------------|--------|-------|
| Task 15 evidence (blind spots) | All phases | ✅ Complete | Available at `.sisyphus/evidence/task-15-blind-spot-quantification.json` |
| Task 17 backlog (safe items) | Phase 1 | ✅ Complete | Available at `.sisyphus/evidence/task-17-data-adjustment-backlog.md` |
| Base branch stability | All phases | ⚠️ TBD | Verify last known good commit before sprint start |
| Test dataset availability | Phase 2 validation | ⚠️ TBD | Must have 24+ hours of capture data |

### 5.2 External Dependencies

| Dependency | Required By | Status | Notes |
|------------|-------------|--------|-------|
| MQTT broker (Mosquitto) | Phase 1 validation | ✅ Required | Telemetry metrics must be visible |
| SQLite (frame DB) | Phase 2 | ✅ Required | For blind spot quantification |
| OIG Box (production) | Phase 4 optional | ❌ Not required | Can validate on existing capture data |
| DNS resolution (for testing) | Phase 1 | ✅ Required | Test environment must resolve `oigservis.cz` |

### 5.3 Blocked Dependencies

**Tasks 13 and 14 (Priority: HIGH Recommendation)**
- **Dependency:** `task-13-standard-request-matrix.json` and `task-14-signal-reaction-matrix.json`
- **Status:** ❌ Unavailable
- **Impact:** Two confidence dimensions cannot be fully scored
- **Mitigation:** Proceed with proxy indicators only; re-quantify once tasks 13/14 complete
- **Decision Point:** Sprint gate review - defer tasks 13/14 to separate sprint

---

## 6. Timeline

### 6.1 Suggested Sprint Schedule (3 weeks)

| Week | Focus | Items | Effort Estimate |
|------|-------|-------|-----------------|
| **Week 1** | Phase 1: Telemetry Instrumentation | DA-001, DA-002, DA-004 | 7 hours |
| **Week 2** | Phase 2: Blind Spot Remediation | Cloud error logging, Mode telemetry | 7 hours |
| **Week 2** | Phase 2: Validation | Re-run quantification, review scores | 3 hours |
| **Week 3** | Phase 3: Documentation | DA-003, DA-006, DA-010, README | 7 hours |
| **Week 3** | Phase 4: Optional Enhancements | Pick 3 of 5 items | 4-12 hours |

**Total Effort:** 28-41 hours (depends on Phase 4 scope)

### 6.2 Gate Reviews

| Review Point | Criteria | Sign-off Required |
|--------------|-----------|------------------|
| **End of Week 1** | Phase 1 gates pass, telemetry visible | Technical Lead |
| **Mid-Week 2** | Blind spots filled, confidence improved | Technical Lead + Architect |
| **End of Week 2** | Quantification shows measurable gains | Product Owner |
| **End of Week 3** | All success criteria met, docs reviewed | Technical Lead + Documentation Reviewer |

### 6.3 Parallel Work Opportunities

**Independent items that can run in parallel:**
- DA-001 (NACK telemetry) and DA-002 (cloud gap histogram) - different files
- DA-003 (cloud reliability docs) and DA-006 (timing docs) - both documentation
- DA-004 (pairing confidence) and DA-005 (frame counters) - different telemetry areas

**Blocked items (must sequence):**
- Cloud error logging → Phase 2 validation (depends on logging implementation)
- Mode telemetry → Phase 2 validation (depends on telemetry implementation)
- Per-direction timestamps → Phase 4 (schema migration, must follow core implementation)

---

## 7. Rollback Procedures

### 7.1 Partial Rollback (Phase-Level)

**If Phase 1 fails:**
```bash
# Identify telemetry commits
git log --oneline --grep="telemetry" -5

# Revert in batch
git revert <telemetry-commit-1> <telemetry-commit-2> <telemetry-commit-3> --no-edit

# Verify rollback
git diff HEAD~3 HEAD  # Should show reversal of telemetry changes

# Re-run tests
./.github/scripts/run_tests.sh

# If tests fail, full rollback to base:
git reset --hard <base-commit-hash>
```

**If Phase 2 fails:**
```bash
# Revert instrumentation changes
git revert <instrumentation-commit-hash>

# Verify blind spots return to baseline
python scripts/protocol_analysis/quantify_blind_spots.py

# Check confidence scores
# Should see overall_confidence = 0.8135 (baseline)
# If not, full rollback required
```

### 7.2 Full Rollback (Sprint-Level)

**If sprint is abandoned:**
```bash
# Save work for potential recovery
git branch follow-up-sprint-aborted

# Rollback to base branch
git checkout main  # or master
git pull origin main

# Delete sprint branch
git branch -D follow-up-sprint-[date]

# Verify clean state
git status  # Should show no uncommitted changes
```

### 7.3 Production Rollback

**If changes deployed to production and issues arise:**

1. **Immediate Actions:**
   ```bash
   # Identify problematic version
   git log --oneline -5

   # Rollback to last known good version
   git revert <deployed-commit-hash>

   # Tag rollback version
   git tag -a v1.X.Y-rollback -m "Rollback from vX.Y.Z due to [issue]"

   # Build and deploy rollback image
   cd addon/oig-proxy
   docker buildx build --platform linux/amd64,linux/arm64 \
     -t ghcr.io/muriel2horak/oig-proxy:1.X.Y-rollback --push .
   ```

2. **Post-Rollback Verification:**
   - [ ] Check logs for error messages stopping
   - [ ] Verify telemetry metrics returning to baseline
   - [ ] Confirm frame capture rate normalizes
   - [ ] Test MQTT discovery entities still visible

3. **Root Cause Analysis:**
   - [ ] Document what failed
   - [ ] Update this handoff with lessons learned
   - [ ] Create follow-up item for fix

---

## 8. Decision Points

### 8.1 Pre-Sprint Decisions

**Decision 1: Sprint Scope**
- **Option A:** Full scope (Phases 1-4) - 28-41 hours
- **Option B:** Critical path only (Phases 1-2) - 14 hours
- **Option C:** Minimum viable (Phase 1 only) - 7 hours
- **Recommendation:** Option A if team capacity allows, else Option B
- **Decision Maker:** Product Owner
- **Deadline:** Before sprint start

**Decision 2: Task 13/14 Handling**
- **Option A:** Defer tasks 13/14 to separate sprint (recommended)
- **Option B:** Add tasks 13/14 to this sprint (extends timeline by 1-2 weeks)
- **Option C:** Accept current proxy-indicator scoring only
- **Recommendation:** Option A - blind spots already identified, no urgency for 13/14
- **Decision Maker:** Technical Architect
- **Deadline:** Before sprint start

### 8.2 Mid-Sprint Decisions

**Decision 3: Phase 4 Scope**
- **Trigger:** End of Week 2 review
- **Criteria:**
  - If confidence gains >= 0.05: Include all 5 optional items
  - If confidence gains 0.02-0.05: Include 3 items
  - If confidence gains < 0.02: Skip Phase 4
- **Decision Maker:** Technical Lead + Product Owner

**Decision 4: Rollback Trigger**
- **Trigger:** Any of the following occur
  - Test coverage drops below 75% after implementation
  - Frame capture rate drops > 10%
  - High-severity errors introduced in logs
  - Confidence scores do not improve after Phase 2
- **Decision Maker:** Technical Lead
- **Action:** Execute full rollback or sprint abandonment

### 8.3 Post-Sprint Decisions

**Decision 5: Production Deployment**
- **Criteria for deploy:**
  - All success criteria met
  - Code review approved
  - Test coverage >= 80%
  - Rollback tested on staging
- **Decision Maker:** Product Owner + Release Manager
- **Timeline:** Within 1 week of sprint completion

**Decision 6: Follow-Up Sprint Planning**
- **Based on:**
  - Remaining medium/low priority items from Phase 4
  - Tasks 13/14 completion
  - Any issues discovered during production
- **Decision Maker:** Product Owner
- **Timeline:** 2 weeks post-deployment

---

## 9. Appendix: Evidence References

### 9.1 Blind Spots (Task 15)

| ID | Severity | Category | Fills Recommendation |
|----|----------|-----------|---------------------|
| cloud_error_events_invisible | HIGH | error_handling | Add structured exception logging |
| mode_transitions_mostly_inferred | HIGH | mode_inference | Emit explicit mode-change telemetry |
| no_pcap_sub_millisecond_timing | MEDIUM | timing_fidelity | Optional pcap capture |
| no_direct_cloud_to_box_latency | MEDIUM | timing_fidelity | Add per-direction timestamps |
| ambiguous_request_response_pairing | MEDIUM | pairing_quality | Accept heuristic cost (protocol limitation) |
| device_id_null_majority | MEDIUM | frame_attribution | Add opt-in pseudonymous tokens |
| nack_reason_coverage_incomplete | LOW | error_handling | Implement full NACK parsing |
| subd_fragments_dropped | LOW | frame_completeness | Document intentional drop |
| proxy_internal_queue_state_invisible | LOW | mode_inference | Add replay_mode + queue_depth columns |

### 9.2 Safe Improvement Backlog (Task 17)

| ID | Priority | Category | Effort |
|----|----------|-----------|--------|
| DA-001 | HIGH | telemetry_logging | 2h |
| DA-002 | HIGH | telemetry_logging | 2h |
| DA-003 | LOW | documentation | 1h |
| DA-004 | HIGH | telemetry_logging | 3h |
| DA-005 | LOW | telemetry_logging | 1h |
| DA-006 | LOW | documentation | 2h |
| DA-007 | LOW | telemetry_logging | 2h |
| DA-008 | LOW | configuration | 2h |
| DA-009 | LOW | telemetry_logging | 1h |
| DA-010 | LOW | documentation | 3h |

---

## 10. Contact Information

**Sprint Leads:**
- Technical Lead: [Name] - [Email]
- Product Owner: [Name] - [Email]
- Documentation Reviewer: [Name] - [Email]

**Emergency Rollback Contacts:**
- On-Call Engineer: [Name] - [Phone]
- Release Manager: [Name] - [Email]

**Escalation Path:**
1. Technical Lead (24 hours)
2. Engineering Manager (48 hours)
3. CTO (72 hours)

---

**Document Status:** ✅ Ready for Sprint Planning
**Last Updated:** 2026-02-19
**Version:** 1.0
