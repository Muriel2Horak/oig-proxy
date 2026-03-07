## Learnings

<!-- Zde budeme zapisovat zjištění a vzorce během práce -->

## Task 1: build_end_time_frame() fix
- Successfully added `<ToDo>GetActual</ToDo>` tag to END frame
- The function now returns END frame with proper tag order: END → Time → UTCTime → ToDo
- Verified the function outputs correct format: `<Frame><Result>END</Result><Time>...</Time><UTCTime>...</UTCTime><ToDo>GetActual</ToDo><CRC>...</CRC></Frame>`
- Updated docstring to reflect the GetActual command addition

## Task 2: maybe_handle_ack() refactoring
- Successfully replaced manual END frame construction with `build_end_time_frame()` call
- Added `build_end_time_frame` to imports from oig_frame module
- Eliminated 12 lines of repetitive code (lines 361-372) with single function call
- Maintained all existing functionality while improving code maintainability

## Task 3 & 4: Revert ACK+GetActual (already done)
- `cloud_forwarder.py`: No changes needed - already correct (no ACK+GetActual before Setting)
- `proxy.py`: No changes needed - already correct (no ACK+GetActual before Setting)
- These tasks were already completed in previous commits/fixes

## Task 5: Test verification (already correct)
- Test `test_forward_frame_intercepts_isnewset_with_pending_setting` already has correct assertion
- No changes needed - test already expects `[setting_frame]` without ACK+GetActual

## Task 6: Tests and deployment
- Tests: Specific Setting delivery test PASSED
- Tests: oig_frame tests PASSED (3/3)
- Deploy: SUCCESS (addon v1.6.1 running in ONLINE mode)
- Proxy listening on port 5710, ready to accept BOX connections

## Overall Summary
- All 6 tasks completed successfully
- BOX should now ACK Settings correctly with proper END frames containing <ToDo>GetActual</ToDo>
- Protocol now matches cloud behavior exactly

## Key Implementation Details
- Added `<ToDo>GetActual</ToDo>` as a literal string after the UTCTime f-string
- Maintained the exact tag order as specified in requirements
- Function continues to return bytes as expected by the calling code
- No changes to other frame building functions (build_ack_only_frame, build_offline_ack_frame, etc.)
- Import statement updated to include the new function: `from oig_frame import build_frame, build_end_time_frame`

## Verification
- Python test confirms the function returns proper END frame with GetActual tag
- The frame format matches the cloud protocol reference from payloads.db analysis
- Tag order is correct: Result → Time → UTCTime → ToDo → CRC
- Grep verification confirms `build_end_time_frame` is used in both import (line 22) and function call (line 361)
- No changes to ACK/NACK detection logic or post-END processing logic

## DRY Principle Application
- Successfully applied DRY principle by centralizing END frame construction
- Reduced code duplication and improved maintainability
- Future changes to END frame format only need to be made in one place (oig_frame.py)

