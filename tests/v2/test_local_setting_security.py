"""Security contracts for the local-setting transaction boundary."""
from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Callable

import pytest

from config import Config
from protocol.frame import (
    FrameStreamAssembler,
    FrameStreamError,
    MAX_FRAME_BYTES,
    StreamErrorCode,
)
from protocol.frames import build_setting_frame
from twin.ack_parser import SettingEvent, derive_event_evidence_id
from twin.handler import TwinControlHandler
from twin.state import EventDisposition, IngressDisposition
from twin.store import TwinCommandStore


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION = ROOT / "addon/oig-proxy"
STORE = PRODUCTION / "twin/store.py"
PRODUCTION_PYTHON = tuple(
    path
    for path in PRODUCTION.rglob("*.py")
    if "tests" not in path.parts and "__pycache__" not in path.parts
)


@dataclass(slots=True)
class _MQTTBoundary:
    """Minimal non-network transport for direct ingress security checks."""

    subscriptions: list[tuple[str, Callable[..., None]]]

    def is_ready(self) -> bool:
        return True

    def subscribe(self, topic: str, callback: Callable[..., None]) -> bool:
        self.subscriptions.append((topic, callback))
        return True

    def unsubscribe(self, _topic: str) -> bool:
        return True


def _learn_device(store: TwinCommandStore) -> None:
    store.observe_device(
        device_id="123",
        observed_at_ms=90,
        observed_wire_id=14_000_000,
        observed_wire_id_set=1_786_000_000,
    )


def _handler(store: TwinCommandStore) -> TwinControlHandler:
    return TwinControlHandler(
        mqtt=_MQTTBoundary([]),  # type: ignore[arg-type]
        store=store,
        device_id="123",
        control_enabled=True,
        loop=asyncio.get_running_loop(),
    )


def test_store_uses_parameterized_sql_for_dynamic_values() -> None:
    tree = ast.parse(STORE.read_text(encoding="utf-8"), filename=str(STORE))
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        if call.func.attr not in {"execute", "executemany"} or not call.args:
            continue
        query = call.args[0]
        assert not isinstance(query, ast.JoinedStr)
        assert not (
            isinstance(query, ast.BinOp)
            and isinstance(query.op, (ast.Add, ast.Mod))
        )
        assert not (
            isinstance(query, ast.Call)
            and isinstance(query.func, ast.Attribute)
            and query.func.attr == "format"
        )


def test_production_has_no_raw_setting_replay_or_wildcard_device_subscription() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in PRODUCTION_PYTHON)
    assert "replay_setting_frame.xml" not in source
    assert "_read_replay_frame_once" not in source
    assert "oig/+/control/set" not in source
    assert "}/+/set/#" not in source


def test_control_default_is_fail_closed() -> None:
    addon = json.loads(
        (PRODUCTION / "config.json").read_text(encoding="utf-8")
    )
    assert addon["options"]["control_mqtt_enabled"] is False
    assert Config.control_mqtt_enabled is False


@pytest.mark.asyncio
async def test_retained_ingress_is_rejected_before_json_decode(
    store: TwinCommandStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _learn_device(store)

    def forbidden_decode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("retained ingress reached JSON decoding")

    monkeypatch.setattr("twin.handler.json.loads", forbidden_decode)
    await _handler(store).handle_message(
        "oig/123/control/set",
        b"CONTROL_SECRET_MARKER",
        True,
        received_at_ms=100,
    )
    assert store.read_latest_ingress().disposition is IngressDisposition.REJECTED_RETAINED


@pytest.mark.asyncio
async def test_ingress_logs_do_not_include_raw_payload_at_info(
    store: TwinCommandStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _learn_device(store)
    secret_marker = "CONTROL_SECRET_MARKER"
    caplog.set_level(logging.INFO)
    await _handler(store).handle_message(
        "oig/123/control/set",
        secret_marker.encode(),
        False,
        received_at_ms=100,
    )
    assert secret_marker not in "\n".join(record.getMessage() for record in caplog.records)


def test_stream_and_held_frame_limits_are_one_mebibyte() -> None:
    assert MAX_FRAME_BYTES == 1_048_576
    assembler = FrameStreamAssembler()
    assembler.feed(
        b"<Frame>" + b"x" * (MAX_FRAME_BYTES - len(b"<Frame>")),
        received_at_ms=1,
    )
    with pytest.raises(FrameStreamError) as captured:
        assembler.feed(b"x", received_at_ms=2)
    assert captured.value.code is StreamErrorCode.BUFFER_OVERFLOW

    dialog_tree = ast.parse(
        (PRODUCTION / "proxy/dialog.py").read_text(encoding="utf-8")
    )
    assert any(
        isinstance(node, ast.Constant) and node.value == 1_048_576
        for node in ast.walk(dialog_tree)
    )


def test_event_evidence_is_deduplicated_by_immutable_identity(
    store: TwinCommandStore,
) -> None:
    _learn_device(store)
    content = "Remotely : tbl_box_prms / MODE: [1]->[2]"
    event = SettingEvent(
        derive_event_evidence_id("123", 55, "2026-08-06 10:12:01", content),
        "123",
        55,
        "2026-08-06 10:12:01",
        content,
        "tbl_box_prms",
        "MODE",
        "1",
        "2",
    )
    first = store.record_event(
        evidence=event,
        received_at_ms=100,
        evidence_frame=b"event",
    )
    duplicate = store.record_event(
        evidence=event,
        received_at_ms=101,
        evidence_frame=b"event",
    )
    assert first.disposition is EventDisposition.UNMATCHED
    assert duplicate.disposition is EventDisposition.DUPLICATE
    assert duplicate.evidence.duplicate_count == 1


def test_setting_serializer_xml_escapes_every_dynamic_text_field() -> None:
    rendered = build_setting_frame(
        device_id="A&B",
        table_name="tbl<box",
        item_name='MO"DE',
        value_text="1 < 2 & 3 > 2's",
        wire_id=1,
        wire_id_set=2,
        wire_dt="2026-08-06 10:11:12",
        tsec_text="2026-08-06 08:11:13",
        ver_text="00001",
    )
    assert b"<ID_Device>A&amp;B</ID_Device>" in rendered.wire_frame
    assert b"<TblName>tbl&lt;box</TblName>" in rendered.wire_frame
    assert b"<TblItem>MO&quot;DE</TblItem>" in rendered.wire_frame
    assert b"<NewValue>1 &lt; 2 &amp; 3 &gt; 2&#x27;s</NewValue>" in rendered.wire_frame
