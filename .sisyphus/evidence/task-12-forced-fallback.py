#!/usr/bin/env python3
"""Evidence: Forced fallback works when twin is killed or disabled."""
import os
import sys
import logging
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../addon/oig-proxy"))

log_capture = []

class LogCapture(logging.Handler):
    def emit(self, record):
        log_capture.append(self.format(record))

capture_handler = LogCapture()
capture_handler.setLevel(logging.DEBUG)
logging.getLogger().addHandler(capture_handler)
logging.getLogger().setLevel(logging.DEBUG)

os.environ["CONTROL_TWIN_FIRST_ENABLED"] = "true"
os.environ["TWIN_ENABLED"] = "true"
os.environ["TWIN_KILL_SWITCH"] = "false"

import importlib
import config as cfg  # type: ignore[import-not-found]

from unittest.mock import MagicMock
from control_settings import ControlSettings  # type: ignore[import-not-found]

results = {}


class _FakeWriter:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.drain_calls = 0
        self._closed = False

    def is_closing(self) -> bool:
        return self._closed

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        self.drain_calls += 1

# --- Scenario A: TWIN_KILL_SWITCH forced ---
mock_proxy_a = MagicMock()
mock_proxy_a._twin = MagicMock()
mock_proxy_a._twin_kill_switch = True
mock_proxy_a.device_id = "test_device"
mock_proxy_a._active_box_writer = _FakeWriter()

cs_a = ControlSettings(mock_proxy_a)
route_a = cs_a.resolve_control_route()
results["kill_switch"] = route_a


async def _send_one_legacy(cs: ControlSettings) -> dict:
    return await cs.send_legacy_to_box(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value="1",
        confirm="New",
    )


legacy_send_result = asyncio.run(_send_one_legacy(cs_a))
writer_a = mock_proxy_a._active_box_writer

# --- Scenario B: CONTROL_TWIN_FIRST_ENABLED=false ---
os.environ["CONTROL_TWIN_FIRST_ENABLED"] = "false"
importlib.reload(cfg)

mock_proxy_b = MagicMock()
mock_proxy_b._twin = MagicMock()
mock_proxy_b._twin_kill_switch = False
mock_proxy_b.device_id = "test_device"

from control_settings import ControlSettings as CS2  # type: ignore[import-not-found]
importlib.reload(sys.modules["control_settings"])
from control_settings import ControlSettings as CS3  # type: ignore[import-not-found]

cs_b = CS3(mock_proxy_b)
route_b = cs_b.resolve_control_route()
results["twin_first_disabled"] = route_b

# --- Scenario C: twin=None ---
os.environ["CONTROL_TWIN_FIRST_ENABLED"] = "true"
importlib.reload(cfg)
importlib.reload(sys.modules["control_settings"])
from control_settings import ControlSettings as CS4  # type: ignore[import-not-found]

mock_proxy_c = MagicMock()
mock_proxy_c._twin = None
mock_proxy_c._twin_kill_switch = False
mock_proxy_c.device_id = "test_device"

cs_c = CS4(mock_proxy_c)
route_c = cs_c.resolve_control_route()
results["twin_unavailable"] = route_c

legacy_markers = [msg for msg in log_capture if "LEGACY_PATH_MARKER" in msg]

evidence_path = os.path.join(os.path.dirname(__file__), "task-12-forced-fallback.txt")
with open(evidence_path, "w") as f:
    f.write("=== Task-12 Evidence: Forced fallback works ===\n\n")

    f.write("--- Scenario A: TWIN_KILL_SWITCH=true ---\n")
    f.write(f"  route: {results['kill_switch']}\n")
    f.write(f"  expected: legacy\n")
    f.write(f"  PASS: {results['kill_switch'] == 'legacy'}\n\n")

    f.write("  legacy send result:\n")
    f.write(f"    ok: {legacy_send_result.get('ok')}\n")
    f.write(f"    route: {legacy_send_result.get('route')}\n")
    f.write(f"    writer writes: {len(writer_a.writes)}\n")
    f.write(f"    writer drain calls: {writer_a.drain_calls}\n")
    f.write(
        f"    PASS: {bool(legacy_send_result.get('ok')) and len(writer_a.writes) == 1 and writer_a.drain_calls == 1}\n\n"
    )

    f.write("--- Scenario B: CONTROL_TWIN_FIRST_ENABLED=false ---\n")
    f.write(f"  route: {results['twin_first_disabled']}\n")
    f.write(f"  expected: legacy\n")
    f.write(f"  PASS: {results['twin_first_disabled'] == 'legacy'}\n\n")

    f.write("--- Scenario C: twin=None (unavailable) ---\n")
    f.write(f"  route: {results['twin_unavailable']}\n")
    f.write(f"  expected: legacy\n")
    f.write(f"  PASS: {results['twin_unavailable'] == 'legacy'}\n\n")

    f.write(f"--- LEGACY_PATH_MARKER log entries: {len(legacy_markers)} ---\n")
    for m in legacy_markers:
        f.write(f"  {m}\n")

    all_pass = all(v == "legacy" for v in results.values())
    send_ok = bool(legacy_send_result.get("ok")) and len(writer_a.writes) == 1 and writer_a.drain_calls == 1
    has_markers = len(legacy_markers) > 0
    f.write(f"\n--- Verdict ---\n")
    f.write(f"All fallback routes resolve to legacy: {all_pass}\n")
    f.write(f"Legacy send completed: {send_ok}\n")
    f.write(f"LEGACY_PATH_MARKER emitted: {has_markers}\n")
    f.write(f"PASS: {all_pass and send_ok and has_markers}\n")

print(f"Results: {results}")
print(f"Legacy markers: {len(legacy_markers)}")
verdict = (
    all(v == "legacy" for v in results.values())
    and bool(legacy_send_result.get("ok"))
    and len(writer_a.writes) == 1
    and writer_a.drain_calls == 1
    and len(legacy_markers) > 0
)
print("PASS" if verdict else "FAIL")
