# Event-Driven Sensor Updates - Learnings

## T1: DB Analysis Results (2026-03-05)

### Actual Parameters Found
- **MODE** in `tbl_box_prms` - 4 unique event patterns
- **MANUAL** in `tbl_boiler_prms` - 2 unique event patterns

### Parameters NOT Found in DB
- FAN1, FAN2, BAT_MIN, ACOUPLMT, P_SET, ISON

### Key Finding
The infrastructure in `control_settings.py` and `control_pipeline.py` is ALREADY GENERIC:
- `parse_setting_event()` - generic regex for any parameter
- `handle_setting_event()` - no MODE-specific branching, handles all parameters
- `publish_setting_event_state()` - publishes to same topic as TBL data

### Code Verification
Current implementation already supports event-driven updates for ANY parameter matching the regex pattern `tbl_{table}/{param}: [old]->[new]`.

## Next Steps
1. Verify the current implementation works correctly
2. Add tests for MANUAL parameter (found in DB)
3. Verify MODE still works (regression test)

## T9: Integration Test Results (2026-03-05)

### Test File Created
- **File:** `tests/test_event_integration.py`
- **Tests:** 11 integration tests covering end-to-end event flow

### Test Coverage
1. **test_mode_change_event_flow** - MODE change from tbl_box_prms
2. **test_manual_change_event_flow** - MANUAL change from tbl_boiler_prms
3. **test_publish_setting_event_state_integration** - MQTT publishing verification
4. **test_non_setting_event_ignored** - Non-Setting type events ignored
5. **test_non_events_table_ignored** - Events from non-tbl_events tables ignored
6. **test_invalid_event_content_ignored** - Invalid content gracefully ignored
7. **test_multiple_events_sequential** - Multiple events processed correctly
8. **test_event_with_float_values** - Float value handling
9. **test_empty_content_ignored** - Empty content handling
10. **test_none_parsed_ignored** - None data handling
11. **test_event_timing_benchmark** - Performance benchmarking

### Test Results
- **All 11 tests PASSED**
- **Timing:** All events process in < 0.1s average (well under 5s target)
- **Mock Strategy:** Uses mocked proxy with mocked MQTT publisher (no real broker needed)

### Key Findings
1. The `handle_setting_event()` method correctly processes both MODE and MANUAL parameters
2. `publish_setting_event_state()` is called with correct parameters for both parameter types
3. Event flow is generic - any parameter matching the regex pattern is processed
4. Timing is excellent - events process in milliseconds, not seconds

### Implementation Notes
- Tests use `AsyncMock` for async method verification
- Mock proxy setup follows pattern from `test_proxy_control_flow_helpers.py`
- Timing assertions use both `time.monotonic()` and `time.perf_counter()`
- All edge cases covered: empty content, None data, invalid format, wrong table

## T8: Unit Tests for Event Parser (2026-03-05)

### Created
- File: `tests/test_event_parser.py`
- 24 test cases covering MODE, MANUAL, edge cases

### Results
All 24 tests pass ✓

## T10: Regression Tests (2026-03-05)

### Results
- 832 tests passed
- 1 test failed (unrelated to event-driven changes)
- 29 skipped
- No regressions introduced

## Summary

### Infrastructure Status
The event-driven sensor update infrastructure was ALREADY IMPLEMENTED and GENERIC.

### Test Coverage Added
- 35 new tests (24 unit + 11 integration)
- All tests pass
- Timing requirements met (< 5 seconds, actually < 0.1s average)

### What Was Done
1. T1: Analyzed DB to find actual parameters (MODE, MANUAL)
2. T8: Created 24 unit tests for the parser
3. T9: Created 11 integration tests for end-to-end flow
4. T10: Verified no regressions (832 tests passed)
