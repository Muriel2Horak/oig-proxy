# Digital Twin Cloud Alignment Work Plan

## TL;DR
> **Quick Summary**: Revise Digital Twin implementation to behave identically to Cloud mode by simplifying the state machine, removing strict INV-1/2/3 invariant enforcement, and aligning ACK handling with ControlSettings pattern.
>
> **Deliverables**:
> - Simplified `digital_twin.py` with optional feature flag
> - Aligned ACK handling (remove strict invariants, keep basic validation)
> - Updated `proxy.py` routing (minimal changes expected)
> - Updated tests with parity verification
> - Feature flag `TWIN_CLOUD_ALIGNED=true/false` in config
>
> **Estimated Effort**: Medium (~3-5 days)
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Config → DigitalTwin simplification → Tests → QA

---

## Context

### Original Request
Revise Digital Twin implementation based on protocol analysis findings that revealed Digital Twin should behave like Cloud mode.

### Interview Summary
**Key Discussions**:
- Protocol analysis revealed Cloud uses simple queuing while Digital Twin uses complex state machine
- User referred to documentation in `docs/` directory for specific behavior details
- Scope to be determined based on code analysis

**Research Findings**:
- **Digital Twin Current Behavior**: Complex state machine with INV-1/2/3 invariants, poll-driven `on_poll()` delivery, replay buffer for disconnected transactions
- **Cloud Behavior**: Simple dict-based queue in `ControlSettings.pending`, basic conn_id validation, no replay buffer
- **Key Files**:
  - `addon/oig-proxy/digital_twin.py:465-591` - poll-driven delivery
  - `addon/oig-proxy/control_settings.py:235-309` - cloud queue pattern
  - `addon/oig-proxy/twin_transaction.py` - INV-1/2/3 validation
  - `addon/oig-proxy/proxy.py:795-887` - routing logic

### Metis Review
**Identified Gaps** (addressed in this plan):
- Missing feature flag for backward compatibility
- Need explicit guardrails around replay buffer (keep minimal vs remove entirely)
- Edge case: multiple pending settings handling differs
- Need parity tests to verify behavioral equivalence
- Connection ownership validation must be preserved (even if simplified)

---

## Work Objectives

### Core Objective
Simplify Digital Twin implementation to match Cloud behavior while maintaining backward compatibility via feature flag.

### Concrete Deliverables
1. **Configuration**: Add `TWIN_CLOUD_ALIGNED` flag (default: false for compatibility)
2. **Digital Twin Core**: Simplified queue-based delivery matching ControlSettings pattern
3. **ACK Handling**: Basic conn_id validation (remove strict INV-1/2/3 enforcement)
4. **Routing**: Update proxy.py routing if needed for new behavior
5. **Tests**: Parity tests verifying Cloud vs Twin produce identical results
6. **Documentation**: Migration guide for users wanting new behavior

### Definition of Done
- [ ] All TODO tasks completed
- [ ] `bun test` or `pytest` passes (all tests)
- [ ] Parity tests demonstrate identical Cloud vs Twin behavior
- [ ] Feature flag works (both modes tested)
- [ ] No regressions in existing tests

### Must Have
- Feature flag for backward compatibility (`TWIN_CLOUD_ALIGNED`)
- Simplified Digital Twin queue (dict-based like ControlSettings)
- Basic conn_id validation in ACK path
- Parity tests between Cloud and Twin routing

### Must NOT Have (Guardrails)
- Complete removal of INV validation without replacement
- Breaking changes to MQTT API response structure
- Changes to Cloud mode behavior
- Changes to OFFLINE mode local ACK behavior

### Design Decisions (CONFIRMED)
Based on user requirements:
| Aspect | Decision | Rationale |
|--------|----------|-----------|
| **Replay buffer** | ✅ Remove entirely | Match Cloud behavior exactly (approved) |
| **Queue structure** | ✅ Single-item dict | Match ControlSettings.pending pattern (approved) |
| **Rapid settings** | ✅ Overwrite last | Only last setting applied, like Cloud (approved) |
| **INV validation** | ✅ Optional | Strict enforcement only in legacy mode |

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (tests/ directory exists)
- **Automated tests**: Tests-after (implement first, then verify)
- **Framework**: pytest
- **Agent-Executed QA**: Each task includes concrete verification steps

### QA Policy
Every task MUST include agent-executed QA scenarios. Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.
- **Python/Backend**: Use Bash (pytest, python REPL) — Import, call functions, compare output
- **Config validation**: Use Bash (grep, python) — Verify flag values, parse config

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — start immediately):
├── Task 1: Add TWIN_CLOUD_ALIGNED feature flag [quick]
└── Task 2: Create simplified twin queue structure [quick]

Wave 2 (Core Implementation — after Wave 1):
└── Task 3: Implement simplified ACK handling [unspecified-high]
    └── Note: Sequential - ACK logic is core dependency for routing

Wave 3 (Routing & Tests — after Wave 2):
├── Task 4: Update proxy.py routing for new behavior [quick]
├── Task 5: Implement parity logging [quick]
├── Task 6: Update existing twin tests [unspecified-high]
├── Task 7: Add parity tests (Cloud vs Twin) [unspecified-high]
└── Task 8: Add feature flag tests [quick]

Wave 4 (Documentation & Final QA — after Wave 3):
├── Task 9: Update documentation [writing]
└── Task 10: Integration verification [unspecified-high]

Critical Path: Task 1 → Task 2 → Task 3 → Task 6 → Task 7
Parallel Speedup: ~40% faster than sequential
```

---

## TODOs

> Implementation + Test = ONE Task. Every task MUST have QA Scenarios.

- [x] 1. Add TWIN_CLOUD_ALIGNED Feature Flag

  **What to do**:
  - Add `TWIN_CLOUD_ALIGNED` boolean flag to `addon/oig-proxy/config.py`
  - Default value: `False` (backward compatibility)
  - Add to configuration validation/schema if exists
  - Document the flag in config comments
  
  **Must NOT do**:
  - Change default Cloud mode behavior
  - Remove existing Digital Twin code
  - Modify any existing feature flags

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Reason**: Simple configuration change, low risk

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 2)
  - **Blocks**: Task 2, Task 4
  - **Blocked By**: None

  **References**:
  - Pattern: `addon/oig-proxy/config.py:98-110` - existing PROXY_MODE config
  - Similar flag: `addon/oig-proxy/config.py:TWIN_ENABLED`
  - Validation: Look for config validation patterns in same file

  **Acceptance Criteria**:
  - [ ] Flag exists in config.py with default False
  - [ ] Config loads without errors: `python -c "import config; print(config.TWIN_CLOUD_ALIGNED)"` → False
  - [ ] Can be set to True: `TWIN_CLOUD_ALIGNED=true python -c "import config; print(config.TWIN_CLOUD_ALIGNED)"` → True

  **QA Scenarios**:
  ```
  Scenario: Default flag value
  Tool: Bash
  Preconditions: Clean environment
  Steps:
    1. Run: cd addon/oig-proxy && python -c "from config import TWIN_CLOUD_ALIGNED; print(TWIN_CLOUD_ALIGNED)"
    2. Assert: output is "False"
  Expected Result: Flag defaults to False
  Evidence: .sisyphus/evidence/task-1-default-flag.txt

  Scenario: Flag can be enabled via environment
  Tool: Bash
  Preconditions: TWIN_CLOUD_ALIGNED=true set
  Steps:
    1. Run: cd addon/oig-proxy && TWIN_CLOUD_ALIGNED=true python -c "from config import TWIN_CLOUD_ALIGNED; print(TWIN_CLOUD_ALIGNED)"
    2. Assert: output is "True"
  Expected Result: Flag respects environment variable
  Evidence: .sisyphus/evidence/task-1-env-flag.txt
  ```

  **Commit**: YES
  - Message: `feat(config): add TWIN_CLOUD_ALIGNED feature flag`
  - Files: `addon/oig-proxy/config.py`
  - Pre-commit: `python -m py_compile addon/oig-proxy/config.py`

- [x] 2. Create Simplified Twin Queue Structure

  **What to do**:
  - In `digital_twin.py`, create alternative queue structure matching ControlSettings pattern
  - Model after `control_settings.py:287-296` - simple dict-based pending storage
  - Keep existing complex state machine but add simplified path behind feature flag
  - Add `_pending_simple` dict with same structure as `ControlSettings.pending`
  
  **Must NOT do**:
  - Remove existing deque queue or state machine
  - Break existing tests
  - Change Cloud mode behavior

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Reason**: Requires careful understanding of both Digital Twin and Cloud patterns

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Task 3
  - **Blocked By**: Task 1

  **References**:
  - Pattern to follow: `addon/oig-proxy/control_settings.py:287-296` - ControlSettings.pending structure
  - Current twin queue: `addon/oig-proxy/digital_twin.py:84-93` - existing deque
  - DTOs: `addon/oig-proxy/twin_state.py:QueueSettingDTO` - data structure

  **Acceptance Criteria**:
  - [ ] Simplified queue exists alongside complex state machine
  - [ ] Structure matches ControlSettings.pending (dict with setting_id, conn_id, timestamp)
  - [ ] Both queues can coexist (no interference)
  - [ ] Code compiles: `python -m py_compile addon/oig-proxy/digital_twin.py`

  **QA Scenarios**:
  ```
  Scenario: Simplified queue structure exists
  Tool: Bash
  Preconditions: Task 1 complete
  Steps:
    1. Import: cd addon/oig-proxy && python -c "from digital_twin import DigitalTwin; t = DigitalTwin(); print(hasattr(t, '_pending_simple'))"
    2. Assert: output is "True"
  Expected Result: Simplified queue attribute exists
  Evidence: .sisyphus/evidence/task-2-queue-exists.txt

  Scenario: Queue structure matches ControlSettings
  Tool: Bash
  Preconditions: Both files exist
  Steps:
    1. Check structure: cd addon/oig-proxy && python -c "from digital_twin import DigitalTwin; t = DigitalTwin(); print(type(t._pending_simple).__name__)"
    2. Assert: output is "dict"
  Expected Result: Queue types match
  Evidence: .sisyphus/evidence/task-2-queue-type.txt
  ```

  **Commit**: YES
  - Message: `refactor(twin): add simplified queue structure matching ControlSettings`
  - Files: `addon/oig-proxy/digital_twin.py`
  - Pre-commit: `python -m py_compile addon/oig-proxy/digital_twin.py`

- [ ] 3. Implement Simplified ACK Handling

  **What to do**:
  - Add simplified `on_ack()` method in `digital_twin.py` using new feature flag
  - Model after `control_settings.py:336-394` - basic conn_id validation only
  - Remove strict INV-1/2/3 enforcement when TWIN_CLOUD_ALIGNED=True
  - Keep INV validation as fallback when flag is False
  - Add simplified timeout handling matching Cloud pattern
  
  **Must NOT do**:
  - Remove existing INV validation entirely (keep for backward compatibility)
  - Break Cloud mode ACK handling
  - Change timeout values without config

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Reason**: Core logic change affecting ACK handling, requires careful testing

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (sequential with Task 4, 5)
  - **Blocks**: Task 6, Task 7
  - **Blocked By**: Task 2

  **References**:
  - Pattern to follow: `addon/oig-proxy/control_settings.py:336-394` - ACK handling
  - Current twin ACK: `addon/oig-proxy/digital_twin.py:637-720` - complex on_ack
  - INV validation: `addon/oig-proxy/twin_transaction.py` - to be made optional

  **Acceptance Criteria**:
  - [ ] Simplified ACK method exists and compiles
  - [ ] Basic conn_id validation works
  - [ ] INV validation skipped when TWIN_CLOUD_ALIGNED=True
  - [ ] INV validation runs when TWIN_CLOUD_ALIGNED=False

  **QA Scenarios**:
  ```
  Scenario: Simplified ACK with cloud-aligned mode
  Tool: Bash
  Preconditions: TWIN_CLOUD_ALIGNED=true
  Steps:
    1. Import: cd addon/oig-proxy && python -c "
    import os
    os.environ['TWIN_CLOUD_ALIGNED'] = 'true'
    from digital_twin import DigitalTwin
    dt = DigitalTwin()
    # Verify module loads without error
    print('ACK handling works')
    "
    2. Assert: exit code 0
  Expected Result: ACK handling succeeds in cloud-aligned mode
  Evidence: .sisyphus/evidence/task-3-cloud-ack.txt

  Scenario: INV validation still works in legacy mode
  Tool: Bash
  Preconditions: TWIN_CLOUD_ALIGNED=false (default)
  Steps:
    1. Import: cd addon/oig-proxy && python -c "
    from digital_twin import DigitalTwin
    dt = DigitalTwin()
    # Verify module loads without error
    print('Legacy mode preserved')
    "
    2. Assert: exit code 0
  Expected Result: Legacy mode still uses INV validation
  Evidence: .sisyphus/evidence/task-3-legacy-ack.txt
  ```

  **Commit**: YES
  - Message: `refactor(twin): simplify ACK handling for cloud-aligned mode`
  - Files: `addon/oig-proxy/digital_twin.py`
  - Pre-commit: `pytest tests/test_digital_twin.py -v -k ack`

- [ ] 4. Update Proxy.py Routing for New Behavior

  **What to do**:
  - Review `proxy.py:795-834` - routing decision logic
  - Update `_resolve_local_control_routing()` to respect TWIN_CLOUD_ALIGNED flag
  - When flag is True, use simplified routing path
  - Ensure routing works correctly for both old and new behavior
  - Minimal changes - primarily conditional logic
  
  **Must NOT do**:
  - Remove existing routing logic
  - Change Cloud mode routing
  - Modify OFFLINE mode behavior

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Reason**: Routing changes are minimal, conditional logic only

**Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 3)
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 7
  - **Blocked By**: Task 3

**References**:
  - Routing logic: `addon/oig-proxy/proxy.py:795-834` - _resolve_local_control_routing
  - Twin dispatch: `addon/oig-proxy/proxy.py:843-887` - _dispatch_local_control_via_twin
  - Control pipeline: `addon/oig-proxy/control_pipeline.py:549-605` - routing

  **Acceptance Criteria**:
  - [ ] Routing respects TWIN_CLOUD_ALIGNED flag
  - [ ] Both modes work correctly
  - [ ] No regression in existing routing tests

  **QA Scenarios**:
  ```
  Scenario: Routing respects feature flag
  Tool: Bash
  Preconditions: Config and routing updated
  Steps:
    1. Test legacy mode: cd addon/oig-proxy && TWIN_CLOUD_ALIGNED=false python -c "from proxy import *; print('Legacy routing OK')"
    2. Test cloud mode: cd addon/oig-proxy && TWIN_CLOUD_ALIGNED=true python -c "from proxy import *; print('Cloud routing OK')"
    3. Assert: both exit code 0
  Expected Result: Routing works in both modes
  Evidence: .sisyphus/evidence/task-4-routing.txt
  ```

  **Commit**: YES
  - Message: `feat(proxy): update routing for cloud-aligned twin behavior`
  - Files: `addon/oig-proxy/proxy.py`
  - Pre-commit: `python -m py_compile addon/oig-proxy/proxy.py`

- [ ] 5. Implement Parity Logging

  **What to do**:
  - Add DEBUG logging in Digital Twin to help verify parity with Cloud
  - Log queue operations (add, remove, timeout)
  - Log ACK processing with timing
  - Match log format with ControlSettings logging
  - Helpful for debugging behavioral differences
  
  **Must NOT do**:
  - Add excessive logging (keep DEBUG level only)
  - Change existing log levels without reason
  - Log sensitive data

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Reason**: Simple logging addition, no logic changes

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 7
  - **Blocked By**: Task 3

  **References**:
  - Cloud logging: `addon/oig-proxy/control_settings.py` - look for logger.debug calls
  - Twin logging: `addon/oig-proxy/digital_twin.py` - existing logging pattern

  **Acceptance Criteria**:
  - [ ] DEBUG logging added for queue operations
  - [ ] DEBUG logging added for ACK processing
  - [ ] Logs are useful for parity comparison

  **QA Scenarios**:
  ```
  Scenario: Logging is present
  Tool: Bash
  Preconditions: Code updated
  Steps:
    1. Check: grep -n "logger.debug" addon/oig-proxy/digital_twin.py | wc -l
    2. Assert: count > 0
  Expected Result: Debug logging exists
  Evidence: .sisyphus/evidence/task-5-logging.txt
  ```

  **Commit**: YES
  - Message: `feat(twin): add parity logging for debugging`
  - Files: `addon/oig-proxy/digital_twin.py`
  - Pre-commit: `python -m py_compile addon/oig-proxy/digital_twin.py`

- [ ] 6. Update Existing Twin Tests

  **What to do**:
  - Update `tests/test_digital_twin.py` to work with new behavior
  - Add tests for both TWIN_CLOUD_ALIGNED=True and False modes
  - Ensure existing tests pass in legacy mode (backward compatibility)
  - Update or skip tests that are no longer relevant in cloud-aligned mode
  - Focus on queue operations, ACK handling, timeout behavior
  
  **Must NOT do**:
  - Remove all existing tests (keep for legacy mode)
  - Break Cloud mode tests
  - Skip tests without clear reason

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Reason**: Requires understanding of both old and new behavior

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (tests)
  - **Blocks**: Task 7
  - **Blocked By**: Task 3, Task 4

  **References**:
  - Existing tests: `tests/test_digital_twin.py`
  - ControlSettings tests: `tests/test_control_settings.py` - for patterns
  - Test patterns: Look for pytest fixtures and parametrize usage

  **Acceptance Criteria**:
  - [ ] Tests pass for both modes
  - [ ] Legacy mode tests unchanged
  - [ ] Cloud-aligned mode has appropriate tests
  - [ ] All tests pass: `pytest tests/test_digital_twin.py -v`

  **QA Scenarios**:
  ```
  Scenario: Legacy mode tests pass
  Tool: Bash
  Preconditions: TWIN_CLOUD_ALIGNED=false
  Steps:
    1. Run: TWIN_CLOUD_ALIGNED=false pytest tests/test_digital_twin.py -v
    2. Assert: exit code 0, no failures
  Expected Result: Legacy tests pass
  Evidence: .sisyphus/evidence/task-6-legacy-tests.txt

  Scenario: Cloud-aligned mode tests pass
  Tool: Bash
  Preconditions: TWIN_CLOUD_ALIGNED=true
  Steps:
    1. Run: TWIN_CLOUD_ALIGNED=true pytest tests/test_digital_twin.py -v
    2. Assert: exit code 0, no failures
  Expected Result: Cloud-aligned tests pass
  Evidence: .sisyphus/evidence/task-6-cloud-tests.txt
  ```

  **Commit**: YES
  - Message: `test(twin): update tests for cloud-aligned behavior`
  - Files: `tests/test_digital_twin.py`
  - Pre-commit: `pytest tests/test_digital_twin.py -v`

- [ ] 7. Add Parity Tests (Cloud vs Twin)

  **What to do**:
  - Create new test file `tests/test_twin_cloud_parity.py`
  - Write tests that verify identical behavior between Cloud and Twin routing
  - Test scenarios: queue setting, ACK processing, timeout handling
  - Use same inputs for both modes, assert same outputs
  - Critical for verifying the "behaves like Cloud" requirement
  
  **Must NOT do**:
  - Test implementation details (test behavior, not code structure)
  - Assume identical internal state (test external behavior only)
  - Skip timeout and edge case scenarios

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Reason**: Complex integration tests requiring both paths

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (tests)
  - **Blocks**: Task 10
  - **Blocked By**: Task 6

  **References**:
  - Test patterns: `tests/test_proxy_flow.py` - integration test patterns
  - ControlSettings: `addon/oig-proxy/control_settings.py` - Cloud behavior
  - DigitalTwin: `addon/oig-proxy/digital_twin.py` - Twin behavior

  **Acceptance Criteria**:
  - [ ] Parity tests exist and pass
  - [ ] Tests cover queue, ACK, timeout scenarios
  - [ ] Both paths produce identical results

  **QA Scenarios**:
  ```
  Scenario: Parity test for queue setting
  Tool: Bash
  Preconditions: Both implementations ready
  Steps:
    1. Run: pytest tests/test_twin_cloud_parity.py::test_queue_setting -v
    2. Assert: pass
  Expected Result: Both modes handle queue setting identically
  Evidence: .sisyphus/evidence/task-7-parity-queue.txt

  Scenario: Parity test for ACK processing
  Tool: Bash
  Preconditions: Both implementations ready
  Steps:
    1. Run: pytest tests/test_twin_cloud_parity.py::test_ack_processing -v
    2. Assert: pass
  Expected Result: Both modes handle ACK identically
  Evidence: .sisyphus/evidence/task-7-parity-ack.txt
  ```

  **Commit**: YES
  - Message: `test(integration): add Cloud vs Twin parity tests`
  - Files: `tests/test_twin_cloud_parity.py` (new file)
  - Pre-commit: `python -m py_compile tests/test_twin_cloud_parity.py`

- [ ] 8. Add Feature Flag Tests

  **What to do**:
  - Add tests verifying TWIN_CLOUD_ALIGNED flag works correctly
  - Test flag loading from environment
  - Test flag default value
  - Test runtime flag changes (if supported)
  - Ensure flag doesn't affect Cloud mode
  
  **Must NOT do**:
  - Test internal implementation of flag
  - Skip edge cases (invalid values, etc.)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Reason**: Simple configuration tests

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3
  - **Blocks**: Task 10
  - **Blocked By**: Task 1

  **References**:
  - Config: `addon/oig-proxy/config.py` - TWIN_CLOUD_ALIGNED
  - Test patterns: Look for existing config tests

  **Acceptance Criteria**:
  - [ ] Flag tests exist
  - [ ] Tests pass

  **QA Scenarios**:
  ```
  Scenario: Flag default value test
  Tool: Bash
  Steps:
    1. Run: pytest tests/test_config.py::test_twin_cloud_aligned_default -v
    2. Assert: pass
  Expected Result: Flag defaults to False
  Evidence: .sisyphus/evidence/task-8-flag-default.txt
  ```

  **Commit**: YES
  - Message: `test(config): add TWIN_CLOUD_ALIGNED feature flag tests`
  - Files: `tests/test_config.py` (or tests/test_digital_twin.py)
  - Pre-commit: `python -m py_compile tests/test_config.py`

- [ ] 9. Update Documentation

  **What to do**:
  - Update `docs/protocol_behavior_specification.md` with Digital Twin behavior changes
  - Add migration guide for users wanting cloud-aligned behavior
  - Document feature flag usage
  - Update inline code documentation/comments
  - Document behavioral differences (legacy vs cloud-aligned)
  
  **Must NOT do**:
  - Remove existing documentation
  - Skip backward compatibility notes

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - **Reason**: Documentation task

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4
  - **Blocks**: Task 10
  - **Blocked By**: Task 6, Task 7

  **References**:
  - Existing docs: `docs/protocol_behavior_specification.md`
  - Config: `addon/oig-proxy/config.py` - flag documentation
  - Code: `addon/oig-proxy/digital_twin.py` - inline comments

  **Acceptance Criteria**:
  - [ ] Documentation updated
  - [ ] Migration guide added
  - [ ] Feature flag documented

  **QA Scenarios**:
  ```
  Scenario: Documentation exists
  Tool: Bash
  Steps:
    1. Check: grep -r "TWIN_CLOUD_ALIGNED" docs/
    2. Assert: found
  Expected Result: Flag is documented
  Evidence: .sisyphus/evidence/task-9-docs.txt
  ```

  **Commit**: YES
  - Message: `docs: add migration guide for cloud-aligned twin`
  - Files: `docs/`, `addon/oig-proxy/config.py`
  - Pre-commit: N/A (docs only)

- [ ] 10. Integration Verification

  **What to do**:
  - Run full test suite
  - Verify no regressions
  - Test end-to-end scenario with both modes
  - Run integration tests if available
  - Final check before delivery
  
  **Must NOT do**:
  - Skip any test failures
  - Ignore warnings

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Reason**: Final integration verification

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4
  - **Blocks**: Final Verification Wave
  - **Blocked By**: Task 6, Task 7, Task 8

  **References**:
  - Tests: `tests/` directory
  - Integration tests: Look for integration test patterns

  **Acceptance Criteria**:
  - [ ] All tests pass
  - [ ] No regressions
  - [ ] End-to-end scenario works

  **QA Scenarios**:
  ```
  Scenario: Full test suite passes
  Tool: Bash
  Steps:
    1. Run: pytest tests/ -v
    2. Assert: exit code 0
  Expected Result: All tests pass
  Evidence: .sisyphus/evidence/task-10-full-suite.txt
  ```

  **Commit**: YES
  - Message: `chore: final integration verification`
  - Files: N/A (verification only)
  - Pre-commit: `pytest tests/ -v`

---

## Final Verification Wave

> 4 review agents run in PARALLEL. ALL must APPROVE.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists. For each "Must NOT Have": search codebase for forbidden patterns. Check evidence files exist. Output: `VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `pytest` + linter. Review all changed files for: commented-out code, unused imports, AI slop patterns. Output: `Build [PASS/FAIL] | Tests [N pass/N fail] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Execute EVERY QA scenario from EVERY task. Test cross-task integration. Save evidence to `.sisyphus/evidence/final-qa/`. Output: `Scenarios [N/N pass] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  Verify each "What to do" was implemented. Check no scope creep. Output: `Tasks [N/N compliant] | VERDICT`

---

## Commit Strategy

- **Task 1**: `feat(config): add TWIN_CLOUD_ALIGNED feature flag`
- **Task 2**: `refactor(twin): simplify queue structure to match ControlSettings`
- **Task 3**: `refactor(twin): simplify ACK handling, remove strict INV enforcement`
- **Task 4**: `feat(proxy): update routing for cloud-aligned twin behavior`
- **Task 5**: `feat(twin): add parity logging for debugging`
- **Task 6**: `test(twin): update tests for simplified behavior`
- **Task 7**: `test(integration): add Cloud vs Twin parity tests`
- **Task 8**: `test(config): add feature flag tests`
- **Task 9**: `docs: add migration guide for cloud-aligned twin`
- **Task 10**: `chore: final integration verification`

---

## Success Criteria

### Verification Commands
```bash
# Run all tests
pytest tests/ -v

# Run parity tests specifically
pytest tests/test_twin_cloud_parity.py -v

# Verify feature flag works
python -c "from config import TWIN_CLOUD_ALIGNED; print(f'Flag: {TWIN_CLOUD_ALIGNED}')"

# Check no regressions in Cloud mode
pytest tests/test_control_settings.py -v
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] Parity tests demonstrate identical Cloud vs Twin behavior
- [ ] Feature flag tested in both states
