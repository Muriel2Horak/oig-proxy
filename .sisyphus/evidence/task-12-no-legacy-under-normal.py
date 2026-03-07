#!/usr/bin/env python3
"""Evidence: Normal operation avoids legacy path when twin-first is ON."""
import os
import sys
import logging
import asyncio
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../addon/oig-proxy"))

os.environ["CONTROL_TWIN_FIRST_ENABLED"] = "true"
os.environ["TWIN_ENABLED"] = "true"
os.environ["TWIN_KILL_SWITCH"] = "false"

log_capture: list[str] = []

class LogCapture(logging.Handler):
    def emit(self, record):
        log_capture.append(self.format(record))

capture_handler = LogCapture()
capture_handler.setLevel(logging.DEBUG)
logging.getLogger().addHandler(capture_handler)
logging.getLogger().setLevel(logging.DEBUG)

import importlib
import config as cfg  # type: ignore[import-not-found]
importlib.reload(cfg)

from digital_twin import DigitalTwin, TwinMQTTHandler  # type: ignore[import-not-found]


class _StubPublisher:
    def add_message_handler(self, *args, **kwargs):
        return None


async def _run() -> list[dict[str, str]]:
    twin = DigitalTwin(session_id="evidence-task-12")
    handler = TwinMQTTHandler(twin=twin, mqtt_publisher=_StubPublisher())

    commands = [
        ("tbl_box_prms", "MODE", "1"),
        ("tbl_invertor_prm1", "AAC_MAX_CHRG", "120.0"),
        ("tbl_box_prms", "SA", "1"),
    ]

    for tbl, item, val in commands:
        topic = f"{cfg.MQTT_NAMESPACE}/{tbl}/{item}/set"
        payload = json.dumps({"new_value": val}).encode("utf-8")
        await handler.on_mqtt_message(topic=topic, payload=payload)

    snapshot = await twin.get_queue_snapshot()
    return [{"tbl": s.tbl_name, "item": s.tbl_item, "tx_id": s.tx_id} for s in snapshot]


queued = asyncio.run(_run())

legacy_hits = [msg for msg in log_capture if "LEGACY_PATH_MARKER" in msg]

evidence_path = os.path.join(os.path.dirname(__file__), "task-12-no-legacy-under-normal.txt")
with open(evidence_path, "w") as f:
    f.write("=== Task-12 Evidence: Normal operation avoids legacy ===\n\n")
    f.write(f"CONTROL_TWIN_FIRST_ENABLED: {cfg.CONTROL_TWIN_FIRST_ENABLED}\n")
    f.write(f"TWIN_ENABLED: {cfg.TWIN_ENABLED}\n")
    f.write(f"TWIN_KILL_SWITCH: {cfg.TWIN_KILL_SWITCH}\n\n")

    f.write("--- Queued commands (twin path) ---\n")
    for r in queued:
        f.write(f"  {r['tbl']}/{r['item']} tx_id={r['tx_id']}\n")

    f.write(f"\n--- LEGACY_PATH_MARKER hits: {len(legacy_hits)} ---\n")
    for hit in legacy_hits:
        f.write(f"  {hit}\n")

    all_queued = len(queued) == 3
    no_legacy = len(legacy_hits) == 0

    f.write(f"\n--- Verdict ---\n")
    f.write(f"All commands queued via twin handler: {all_queued}\n")
    f.write(f"Zero unintended legacy hits: {no_legacy}\n")
    f.write(f"PASS: {all_queued and no_legacy}\n")

print(f"Legacy hits: {len(legacy_hits)}")
print(f"Queued: {len(queued)}")
print("PASS" if len(queued) == 3 and len(legacy_hits) == 0 else "FAIL")
