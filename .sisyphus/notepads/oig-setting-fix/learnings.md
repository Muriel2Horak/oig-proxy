# Learnings: OIG Setting Fix

## 2025-02-17: Integration Test - Setting Delivery on All Poll Types

### Test Results
- ✓ IsNewFW poll receives Setting frame when pending
- ✓ IsNewWeather poll receives Setting frame when pending
- ✓ IsNewSet poll receives Setting frame when pending
- ✓ ACK with `<Reason>Setting</Reason>` clears pending
- ✓ Subsequent polls get END (no pending setting)
- ✓ GET /api/pending returns null after ACK

### Key Implementation Details

1. **HTTP API Endpoints Added to Mock Server** (`/Users/martinhorak/Projects/oig-diagnostic-cloud/server.py`):
   - `POST /api/queue-setting` - Queue a setting for delivery
   - `GET /api/pending` - Returns current pending setting or null

2. **Poll Detection Logic** (line 606-608 in server.py):
   ```python
   is_poll = (
       result in ("IsNewSet", "IsNewFW", "IsNewWeather")
       or table_name.endswith(("IsNewSet", "IsNewFW", "IsNewWeather"))
   )
   ```

3. **Setting Frame Delivery** (line 612-620):
   - Any poll type triggers setting delivery if pending_setting is not None
   - Frame built by `_build_setting_frame()` includes all required OIG fields

4. **ACK Handling** (line 398-410):
   - `result == "ACK"` and `reason == "Setting"` clears pending_setting
   - Responds with END to confirm

### Frame Format
Setting frame contains:
- ID, ID_Device, ID_Set, ID_SubD
- DT (local timestamp), NewValue, Confirm=New
- TblName, TblItem, ID_Server
- mytimediff=0, Reason=Setting, TSec (UTC), ver=55734
- CRC (appended by local_oig_crc.build_frame())

### Test Command Sequence
```bash
# Start server
DATA_DIR=/tmp/oig-test-data PORT=5710 WEB_PORT=8080 python3 server.py

# Queue setting
curl -X POST http://localhost:8080/api/queue-setting \
  -H "Content-Type: application/json" \
  -d '{"tbl_name":"tbl_prms","tbl_item":"MODE","new_value":"0"}'

# Check pending
curl http://localhost:8080/api/pending
```
