#RP|#WJ|## [2026-03-02] Session start
#PX|#KM|## [2026-03-02] Task 1: DB Schema Validation - Issue Encountered
#YN|#NS|- Plan: control-debug.md (12 tasks + 3 final review)
#SH|#RX|- DB path: /mnt/data/supervisor/addons/data/d7b5d5b1_oig_proxy/payloads.db
#KX|#NK|- SSH alias: ha, container: addon_d7b5d5b1_oig_proxy
#JN|#BP|- Device ID: 2206237016
#HB|#BX|- Deploy: bash deploy_to_haos.sh
#ZW|#JH|- Tests: python -m pytest tests/ -q (621+ expected)
#BY|#KT|- Branch: fix/setting-frame-fresh-timestamps
#TY|#SM|- Worktree: /Users/martinhorak/projects/oig-proxy
#ZT|#L1|### Task 1: DB Schema + Data Validation
#XZ|#L2|**Issue:** Bash environment not properly configured
#NY|#L3|**Error:** ENOENT: no such file or directory, posix_spawn '/bin/zsh'
#WS|#L4|**Impact:** Unable to automatically execute SSH/database queries
#JM|#L5|
#KK|#L6|### Manual Queries Required
#RS|#L7|1. **Schema:** `sqlite3 /mnt/data/supervisor/addons/data/d7b5d5b1_oig_proxy/payloads.db '.schema frames'`
#MZ|#L8|2. **Total count:** `SELECT COUNT(*) FROM frames`
#XZ|#L9|3. **Direction breakdown:** `SELECT direction, COUNT(*) FROM frames GROUP BY direction`
#WM|#L0|4. **Setting frames (specific):** `SELECT direction, COUNT(*) FROM frames WHERE raw LIKE '%<Reason>Setting</Reason>%' GROUP BY direction`
#NR|#LA|5. **Setting frames (broad):** `SELECT direction, COUNT(*) FROM frames WHERE raw LIKE '%Setting%' GROUP BY direction`
#YN|#LB|6. **Sample frames:** `SELECT ts, direction, conn_id, substr(raw, 1, 150) FROM frames WHERE raw LIKE '%Setting%' ORDER BY ts DESC LIMIT 5`
#KV|#LC|
#HM|#LD|### Success Criteria (pending verification)
#BT|#LE|- [ ] Schema validated: frames table with expected columns
#NW|#LF|- [ ] At least 1 setting frame in proxy_to_box direction
#BZ|#LG|- [ ] At least 1 setting frame in cloud_to_proxy direction
#ZW|#LH|- [ ] Total frame count > 0
#SZ|#LI|
#MN|#LJ|### Resolution
#SX|#LK|Environment configuration needed before automated execution can proceed.
#XH|## [2026-03-02] Task 1: DB Validation Results
#QY|
#HB|- DB INSIDE container at /data/payloads.db (not on host filesystem)
#YK|- Access via: ssh ha "sudo docker exec addon_d7b5d5b1_oig_proxy python3 -c ..."
#VZ|- sqlite3 NOT installed in container - use python3 sqlite3 module
#HW|- Total: 52,130 frames, 2 days of data (Feb 28 - Mar 2)
#NH|- CRITICAL: proxy_to_box has ONLY GetActual frames (1,350) - ZERO setting frames
#NM|- Cloud-originated settings work: 37 cloud_to_proxy + 46 box_to_proxy events
#QS|- conn_id rotates every ~3 min (BOX reconnects frequently!)
#HK|- BOX ACKs for settings come as tbl_events frames with Type=Setting, NOT <Result>ACK>
#QY|- No ACK/NACK frames with <Result>ACK</Result> exist for any settings
#PW|- IsNewSet polls happen ~every 3-4 min (not 10-30s as expected)
#PB|
#MJ|## [2026-03-02] Tasks 2/3/4: DB Analysis Results
#TJ|
#TZ|### T2 - Frame Comparison:
#RX|- Cloud frames use random <ver> (5-digit padded): 01550, 23822, 12897, 11211, 55480
#XK|- Local code (control_settings.py): HARDCODED <ver>55734</ver>
#MB|- cloud_forwarder.py (hybrid/online): REBUILDS frame with random ver -> correct
#ZH|- proxy.py (offline): uses pre-built pending_frame with static ver=55734
#MT|- cloud_forwarder.py calls capture_payload -> proxy_to_box captured
#ZH|- proxy.py does NOT call capture_payload -> no DB trace
#YW|- Zero proxy_to_box setting frames = MQTT settings never reached delivery or never tried
#TW|
#WY|### T3 - ACK Detection BROKEN:
#PS|- BOX confirmation = tbl_events frame with <Type>Setting</Type>
#JW|- BOX NEVER sends <Result>ACK</Result> for settings
#RH|- maybe_handle_ack() requires <Reason>Setting</Reason> + <Result>ACK</Result> TOGETHER
#BN|- This condition is NEVER met -> pending state never clears
#VB|- Cloud settings work because cloud manages its own ACK externally
#YX|- Our code would leave `pending` forever (until timeout)
#JN|
#RT|### T4 - Timeline:
#SK|- IsNewSet polls: every 40-260s (avg ~80s)
#QQ|- conn_id increments 3-4 per poll period
#ST|- Timing should be fine for delivery (40-80s max wait for next poll)
#TV|- ack_timeout_s needs to be > max poll interval (260s)
#TV|## [2026-03-02] Task 5: CTRL_DIAG Diagnostic Logging
#TR|
#QB|### Task Execution
#BX|**Goal**: Add exactly 3 CTRL_DIAG diagnostic logging lines to control_pipeline.py at specified locations
#HV|**Status**: ✅ COMPLETED
#HM|
#YJ|### Implementation Details
#WN|**Locations added**:
#XR|1. **Line 600**: `on_mqtt_message()` function - MQTT message reception
#RX|   - Logs: topic, message length, timestamp
#LQ|   - Context: Right after initial validation, before processing
#YM|
#KX|2. **Line 676**: `maybe_start_next()` method - task scheduling  
#YR|   - Logs: queue length, timestamp
#SZ|   - Context: Inside queue length check, before scheduling logic
#VZ|
#BP|3. **Line 729**: `start_inflight()` method - delegation to send_to_box()
#TJ|   - Logs: table name, item, value, timestamp  
#XQ|   - Context: Just before calling `self._proxy._cs.send_to_box()`
#JN|
#TX|### Technical Approach
#RW|**Success factors**:
#PK|- Used existing `_get_current_timestamp()` helper function (imported from datetime)
#XM|- Verified variable names match actual code context at each location
#KQ|- Maintained existing logging style with `logger.info()`
#QW|- Zero impact on control flow - pure diagnostic logging only
#LZ|- Used `len(payload) if payload else 0` for safe message length calculation
#VJ|- Used `len(self.queue)` directly (queue is a deque, hasattr check not needed)
#RY|
#WK|### Verification Results
#YT|✅ Exactly 3 CTRL_DIAG log lines added (grep count: 3)
#SM|✅ All logs use correct variable names from their respective contexts
#XN|✅ All logs include timestamp using existing helper function
#TW|✅ Zero changes to business logic or control flow
#XS|✅ Evidence saved to `.sisyphus/evidence/task-5-grep.txt`
#NZ|
#JN|### Log Format Pattern
#YB|All logs follow consistent format:
#QR|```
#PT|CTRL_DIAG <operation_type> | <key_data_points> ts=<timestamp>
#XX|```
#TQ|This enables easy filtering and parsing for diagnostic analysis.
#NZ|
#WZ|### Next Steps
#JW|These diagnostic logs will provide visibility into:
#QS|- MQTT message reception patterns
#QW|- Queue scheduling behavior  
#PL|- Delegation timing to send_to_box()
#NY|Essential for debugging control pipeline flow issues.
#2026-03-07
- Proxy control tests relied on removed legacy APIs (`proxy._ctrl` behaviors and `ControlSettings.send_to_box` paths); refactored API surface is now centered on `ControlPipeline` lightweight helpers plus `ControlSettings.queue_setting/get_health/handle_setting_event`.
- Stable migration pattern for tests: assert current public/static helpers (`coerce_value`, `format_tx`, `format_result`, `parse_setting_event`) and async no-op safety hooks (`maybe_start_next`, `observe_box_frame`, `on_box_setting_ack`, `publish_setting_event_state`, `publish_restart_errors`).
- Importing addon modules in tests can be made robust with `importlib.import_module(...)` to avoid Pylance/LSP unresolved-import noise from test environment path layout.
