# Decisions

## [2026-02-17] Task Selection Strategy
- Using TODO section as source of truth (8 tasks)
- Ignoring wave structure mismatches
- Starting with Wave 1 parallelizable tasks: T1, T3

## [2026-02-17] Task 3: Feature Flag + Rollback Gate Specification

### Feature Flag Design Decisions

#### 1. Feature Flag Architecture
- **Decision**: Use 4 primary feature flags for granular control
- **Rationale**: Allows independent testing and rollback of different logic components
- **Flags Defined**:
  - `FEATURE_NEW_OFFLINE_LOGIC_ENABLED`: Controls offline processing logic
  - `FEATURE_NEW_MOCK_LOGIC_ENABLED`: Controls mock/response simulation (depends on offline logic)
  - `FEATURE_HYBRID_AUTO_FAILOVER_ENABLED`: Controls automatic failover behavior
  - `FEATURE_NEW_RETRY_LOGIC_ENABLED`: Controls retry timing calculations

#### 2. Feature Flag Configuration Method
- **Decision**: Prioritize environment variables with JSON config file backup
- **Rationale**: Environment variables are reliable and easy to audit, JSON provides fallback
- **Implementation**: Both methods supported with environment taking precedence

#### 3. Feature Flag Safety
- **Decision**: Default all new feature flags to `false` (safe legacy behavior)
- **Rationale**: Ensures system remains operational even if configuration is missing
- **Implementation**: Feature flag library defaults to safe values when undefined

### Rollback Gate Specification Decisions

#### 4. Gate Design Principles
- **Decision**: Implement strictly binary (pass/fail) gates
- **Rationale**: Eliminates ambiguity in validation results
- **Implementation**: Each gate script returns exit code 0 (pass) or 1 (fail)

#### 5. Critical Gate Categories
- **Decision**: Define 7 mandatory gates across 2 categories (Critical + Operational)
- **Rationale**: Comprehensive coverage of functionality, performance, and operations
- **Critical Gates** (4): Feature flag stability, error rates, performance, functional validation
- **Operational Gates** (3): Log analysis, user acceptance, backup system verification

#### 6. Gate Stability Requirements
- **Decision**: 30-day minimum stability period for feature flags
- **Rationale**: Ensures sufficient time to identify edge cases and performance issues
- **Implementation**: Tracks flag changes in database with timestamp verification

#### 7. Performance Comparison Strategy
- **Decision**: Allow 10% performance degradation for new logic
- **Rationale**: New logic may have additional overhead but should not significantly impact users
- **Implementation**: Automated metrics comparison with floating-point arithmetic

#### 8. Rollback Automation
- **Decision**: Implement automatic rollback on gate failure
- **Rationale**: Minimizes manual intervention and reduces recovery time
- **Implementation**: Gate check script triggers rollback sequence when failures detected

### Enable/Disable Procedure Decisions

#### 9. Multi-Method Enablement
- **Decision**: Support both config file and runtime API methods
- **Rationale**: Config file for persistence, API for immediate changes
- **Implementation**: Priority order: Runtime API → Environment → Config file

#### 10. Graceful Rollback Strategy
- **Decision**: Implement 300-second drain period before disabling offline logic
- **Rationale**: Allows existing requests to complete gracefully
- **Implementation**: Sequential flag disablement with delay between steps

#### 11. Emergency Rollback Protocol
- **Decision**: Single-command emergency rollback capability
- **Rationale**: Critical for quickly recovering from unstable deployments
- **Implementation**: Force disable all flags with automatic config backup

#### 12. Comprehensive Verification
- **Decision**: Multi-step verification for all enable/disable operations
- **Rationale**: Ensures changes take effect and don't break the system
- **Implementation**: Service status, API accessibility, error rate, and log checks

### Testing and Validation Decisions

#### 13. Evidence File Structure
- **Decision**: Create specific evidence files for each test scenario
- **Rationale**: Provides clear audit trail and reproducible test results
- **Files Created**:
  - `task-3-feature-flag-spec.md`: Complete feature flag specification
  - `task-3-rollback-gate-spec.md`: Rollback gate specification with binary checklist
  - `task-3-enable-disable-procedure.md`: Operational procedures documentation
  - `task-3-gate-dry-run.txt`: Gate dry-run test results
  - `task-3-checklist-failure.txt`: Negative test case validation

#### 14. Automated Gate Validation
- **Decision**: Shell script-based validation with comprehensive error reporting
- **Rationale**: Simple, reliable, and easily integrated into CI/CD pipelines
- **Implementation**: Bash scripts with clear exit codes and detailed error messages

#### 15. Missing Criterion Detection
- **Decision**: Proactive validation of required gate scripts
- **Rationale**: Prevents incomplete deployments and ensures all safety checks are in place
- **Implementation**: Pre-flight check validates all 7 required gates exist

### Key Technical Considerations

#### 16. Dependency Management
- **Decision**: Mock logic depends on offline logic being enabled
- **Rationale**: Mock functionality is subset of offline processing
- **Implementation**: Dependency check in gate logic prevents invalid combinations

#### 17. Monitoring Integration
- **Decision**: Integrate gate checks with existing monitoring/alerting systems
- **Rationale**: Provides visibility into rollback events and system health
- **Implementation**: HTTP alerts for gate failures and rollback events

#### 18. Audit Trail Requirements
- **Decision**: Comprehensive logging of all gate executions and flag changes
- **Rationale**: Essential for post-mortem analysis and compliance
- **Implementation**: Structured log format with timestamps, operators, and results

### Impact on Future Tasks

#### 19. Blocking Dependencies Established
- **Impact**: T3 blocks T7, T8, T17 as planned
- **Rationale**: Feature flags and rollback gates must be established before alignment work
- **Follow-up**: Subsequent tasks should verify flag functionality in their test plans

#### 20. Documentation Strategy
- **Decision**: Create comprehensive, self-contained specification documents
- **Rationale**: Reduces dependency on tribal knowledge and enables independent execution
- **Implementation**: Each document includes rationale, procedures, and verification steps

---

**Next Steps**: These feature flags and rollback gates provide the foundation for safe migration from legacy to new offline+mock logic. All subsequent tasks should consider the flag states and gate requirements in their implementation and testing.

---

## [2026-02-17] Task 5: Hybrid Mode Rescue Mechanisms

### Architecture Decisions

#### 21. Per-Frame Rescue Strategy
- **Decision**: Emit local ACK for failed frames WITHOUT immediate mode transition
- **Rationale**: Prevents mode thrashing on transient failures; only transition after sustained failures
- **Implementation**: `cloud_forwarder.handle_timeout()` calls `fallback_offline()` for ACK emission but mode transition requires `fail_count >= threshold`

#### 22. Threshold-Based Hysteresis
- **Decision**: Require consecutive failures exceeding threshold before global fallback
- **Rationale**: Provides stability against intermittent cloud issues
- **Implementation**: `HYBRID_FAIL_THRESHOLD` config (default=1, can be increased for tolerance)

#### 23. Retry Probe Strategy
- **Decision**: Continue probing cloud during offline-state at regular intervals
- **Rationale**: Ensures eventual recovery when cloud becomes available
- **Implementation**: `should_try_cloud()` returns True after `HYBRID_RETRY_INTERVAL` (default=60s)

#### 24. Single-Success Recovery
- **Decision**: Single cloud success immediately returns to online-state
- **Rationale**: Minimizes unnecessary offline time once cloud recovers
- **Implementation**: `record_success()` resets `fail_count=0` and `in_offline=False`

#### 25. END Frame Special Handling
- **Decision**: For END frames in hybrid timeout, send local END without full fallback
- **Rationale**: END frames should not trigger ACK fallback logic
- **Implementation**: `handle_timeout()` has special case for `table_name == "END"`

### Evidence Files Created

- `task-5-hybrid-rescue.txt`: Per-frame timeout rescue documentation
- `task-5-global-fallback.txt`: Threshold-driven global fallback documentation

### Test Coverage

- All 6 hybrid mode tests pass in `tests/test_proxy_modes.py`
- Key test cases:
  - `test_hybrid_no_fallback_before_threshold`: Verifies per-frame rescue
  - `test_hybrid_fallback_after_threshold`: Verifies global fallback
  - `test_hybrid_record_success_resets`: Verifies recovery path
---

## [2026-02-17] Task 4: Mock Poll/Session State Machine Alignment

### Decision 21: No Code Changes Required for Mock Alignment

- **Context**: Task 4 required aligning mock response decisions to contract matrix
- **Analysis**: Verified mock behavior against 183,331 frames from real cloud captures
- **Finding**: Mock was already aligned with contract for all core scenarios
- **Decision**: Accept current implementation as-is, create evidence files only
- **Rationale**: 
  - IsNewSet → END+Time+UTCTime matches real cloud (99.5% of cases)
  - IsNewFW → bare END matches real cloud (99.9% of cases)
  - IsNewWeather → bare END is acceptable simplification (real cloud also sends weather data)
  - Setting delivery context gate correctly implemented
  - BOX ACK handling correctly clears pending and sends END

### Decision 22: Single-Slot Setting Queue Design Accepted

- **Context**: Real cloud can deliver multiple Settings in sequence (multi-slot)
- **Analysis**: Real cloud sends Setting → BOX ACK → Setting → BOX ACK → END
- **Current Mock**: Single-slot, sends END after first BOX ACK
- **Decision**: Accept single-slot design as valid simplification
- **Rationale**:
  - Simplifies state management
  - Sufficient for testing scenarios (one setting at a time)
  - BOX behavior for single setting is correctly validated
  - Multi-setting scenarios are rare (0.25% of IsNewSet polls)

### Decision 23: Contract Matrix "Echo" Terminology Clarification

- **Context**: T2 contract matrix stated "Cloud echoes same frame type only"
- **Finding**: This was misleading - actual response is END or Setting, not echo
- **Decision**: Document clarification, no code changes
- **Corrected Understanding**:
  - "Echo" in contract refers to response categorization
  - IsNewSet poll gets categorized under "IsNewSet" in database
  - Actual frame content is END (99.75%) or Setting (0.25%)

### Decision 24: Setting Delivery Modes Retained

- **Context**: Mock supports 3 delivery modes: poll, isnewset, immediate
- **Analysis**:
  - `poll` mode: Deliver on any IsNew* - matches real cloud
  - `isnewset` mode: Deliver only on IsNewSet - valid subset
  - `immediate` mode: Deliver on any frame - TESTING ONLY
- **Decision**: Retain all modes, document `immediate` as test-only
- **Rationale**: Provides flexibility for different test scenarios

### Decision 25: Weather Data Simulation Not Required

- **Context**: Real cloud sends weather data in response to IsNewWeather (82.9%)
- **Current Mock**: Returns bare END for IsNewWeather
- **Decision**: Accept bare END as sufficient for mock purposes
- **Rationale**:
  - Weather data is not part of setting handshake validation
  - Mock's purpose is proxy/BOX behavior testing, not weather simulation
  - Bare END correctly signals "no data available" to BOX

### Impact on Subsequent Tasks

- **T5 (Proxy offline/hybrid response alignment)**: Can proceed, mock behavior verified
- **T13, T14 (Comparison suite)**: Mock provides contract-valid responses
- **T18 (Regression sweep)**: No mock changes needed, existing tests remain valid

### Evidence Trail

- `.sisyphus/evidence/task-4-mock-setting-handshake.json`: Contract alignment verification
- `.sisyphus/evidence/task-4-out-of-context-blocked.json`: Context gate verification
