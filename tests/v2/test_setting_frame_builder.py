"""Tests for the setting-frame builder: DT/TSec derivation and msg_id sequencing."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib
import xml.etree.ElementTree as ET


frames = importlib.import_module("protocol.frames")
TwinDelivery = importlib.import_module("twin.delivery").TwinDelivery


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        current = datetime(2026, 5, 17, 10, 17, 45, tzinfo=timezone.utc)
        if tz is None:
            return current.replace(tzinfo=None)
        return current.astimezone(tz)


def _frame_tags(frame_bytes: bytes) -> dict[str, str]:
    root = ET.fromstring(frame_bytes.decode("utf-8").strip())
    return {child.tag: child.text or "" for child in root}


def test_build_setting_frame_derives_czech_dt_from_id_set_in_summer(monkeypatch) -> None:
    """DT is the Czech civil time derived from id_set (summer = UTC+2)."""
    monkeypatch.setattr(frames, "datetime", _FrozenDateTime)

    frame = frames.build_setting_frame(
        device_id="2111232079",
        table="tbl_box_prms",
        key="MODE",
        value=1,
        id_set=1779013060,
        msg_id=14409605,
    )

    tags = _frame_tags(frame)
    assert tags["ID"] == "14409605"
    assert tags["ID_Set"] == "1779013060"
    assert tags["DT"] == "17.05.2026 12:17:40"
    assert tags["TSec"] == "2026-05-17 10:17:45"
    assert tags["ID_Server"] == "9"


def test_build_setting_frame_keeps_tsec_at_or_after_id_set(monkeypatch) -> None:
    """TSec is clamped to id_set when the local clock is behind it."""
    monkeypatch.setattr(frames, "datetime", _FrozenDateTime)
    id_set = int(datetime(2026, 5, 17, 10, 17, 45, tzinfo=timezone.utc).timestamp()) + 1

    frame = frames.build_setting_frame(
        device_id="2111232079",
        table="tbl_box_prms",
        key="MODE",
        value=1,
        id_set=id_set,
        msg_id=14409605,
    )

    tags = _frame_tags(frame)
    tsec_epoch = int(
        datetime.strptime(tags["TSec"], "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )
    assert tsec_epoch >= id_set


def test_next_msg_id_continues_after_observed_cloud_id_above_14m() -> None:
    """next_msg_id continues sequentially from the highest observed cloud msg_id."""
    delivery = TwinDelivery(twin_queue=object(), mqtt=object())
    delivery.observe_msg_id(14416650)

    assert delivery.next_msg_id() == 14416651
