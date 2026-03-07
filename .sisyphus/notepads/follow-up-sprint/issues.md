
## [F2 Code Quality Review - 2026-02-20]

### CRITICAL: Deadlock in telemetry_collector.py (_flush_log_buffer)

**File:** `addon/oig-proxy/telemetry_collector.py`
**Introduced by:** commit `0c06a58` (fix: telemetry_collector thread safety)
**Still present in:** current HEAD after `4fa5a5f`

**Root cause:**
- `_flush_log_buffer()` (line 186) acquires `self._logs_lock`
- Inside, it calls `_snapshot_logs()` (line 187)  
- `_snapshot_logs()` (line 178) ALSO tries to acquire `self._logs_lock`
- `threading.Lock()` is non-reentrant → **deadlock**

**Impact:** 4 tests in `tests/test_proxy_internal.py` hang indefinitely:
- `test_collect_telemetry_metrics_flushes_window_metrics`
- `test_cloud_online_success_wins_over_failure`
- `test_cloud_online_eof_short_without_success_is_false`
- `test_telemetry_stats_pairing_and_flush`

These tests hang because they trigger `collect_metrics()` → `_get_telemetry_logs()` → `_flush_log_buffer()` in scenarios where `debug_windows_remaining > 0`.

**Fix:** Either:
1. Make `_snapshot_logs()` NOT acquire the lock (it should be called only while lock is held), OR
2. Use `threading.RLock()` (reentrant lock) instead of `threading.Lock()`

Recommended fix (1): Remove `with self._logs_lock:` from `_snapshot_logs()` since `_flush_log_buffer` already holds it.

