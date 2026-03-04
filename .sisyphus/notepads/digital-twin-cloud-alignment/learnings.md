# Task 1: Add TWIN_CLOUD_ALIGNED flag

## Summary
Added `TWIN_CLOUD_ALIGNED` boolean feature flag to `addon/oig-proxy/config.py` with default `False`.

## Implementation
- Location: Twin Configuration section (lines 249-252)
- Pattern used: `_get_bool_env("TWIN_CLOUD_ALIGNED", False)` - same as `TWIN_ENABLED`
- Added comment explaining flag purpose (necessary for config documentation)

## Verification
- ✅ Syntax check: `python3 -m py_compile addon/oig-proxy/config.py` - passes
- ✅ Default value: `from config import TWIN_CLOUD_ALIGNED` → prints `False`
- ✅ Environment variable: `TWIN_CLOUD_ALIGNED=true` → prints `True`

## Evidence files
- `.sisyphus/evidence/task-1-default-flag.txt`
- `.sisyphus/evidence/task-1-env-flag.txt`

## Notes
- LSP errors in `tests/test_digital_twin.py` are pre-existing (unrelated imports)
- Flag follows existing pattern from `TWIN_ENABLED`, `TWIN_KILL_SWITCH`, etc.
- Backward compatible: default `False` preserves existing behavior
