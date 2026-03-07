from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_MODULE_PATH = Path(__file__).resolve().parents[1] / "addon" / "oig-proxy" / "control_pipeline.py"
_SPEC = importlib.util.spec_from_file_location("control_pipeline", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
ControlPipeline = _MODULE.ControlPipeline


def test_control_pipeline_init_defaults():
    ctrl = ControlPipeline(object())

    assert isinstance(ctrl.session_id, str)
    assert ctrl.session_id
    assert ctrl.mqtt_enabled is False
    assert ctrl.qos == 1
    assert ctrl.queue == []
    assert ctrl.inflight is None
    assert ctrl.last_result is None
    assert ctrl.log_path is None


def test_format_tx_handles_missing_attempts():
    tx = {
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": 3,
        "stage": "queued",
        "tx_id": "tx-1",
    }

    assert (
        ControlPipeline.format_tx(tx)
        == "tbl_box_prms/MODE=3 (queued) tx=tx-1"
    )


def test_format_tx_includes_attempts_when_present():
    tx = {
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": 3,
        "stage": "retry",
        "_attempts": 2,
        "tx_id": "tx-2",
    }

    assert (
        ControlPipeline.format_tx(tx)
        == "tbl_box_prms/MODE=3 (retry 2) tx=tx-2"
    )


def test_format_tx_empty_input_returns_empty_string():
    assert ControlPipeline.format_tx(None) == ""
    assert ControlPipeline.format_tx({}) == ""


def test_format_result_with_and_without_error():
    ok_result = {
        "status": "accepted",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": 2,
        "tx_id": "tx-ok",
    }
    err_result = {
        "status": "error",
        "tbl_name": "tbl_box_prms",
        "tbl_item": "MODE",
        "new_value": 2,
        "error": "invalid",
        "tx_id": "tx-err",
    }

    assert (
        ControlPipeline.format_result(ok_result)
        == "accepted tbl_box_prms/MODE=2 tx=tx-ok"
    )
    assert (
        ControlPipeline.format_result(err_result)
        == "error tbl_box_prms/MODE=2 err=invalid tx=tx-err"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (True, True),
        (7, 7),
        (1.5, 1.5),
        ("true", True),
        ("FALSE", False),
        (" 12 ", 12),
        ("-5", -5),
        ("3.25", 3.25),
        ("abc", "abc"),
        ("1.2.3", "1.2.3"),
    ],
)
def test_coerce_value(raw, expected):
    assert ControlPipeline.coerce_value(raw) == expected


@pytest.mark.asyncio
async def test_async_methods_are_safe_noops():
    ctrl = ControlPipeline(object())

    await ctrl.publish_restart_errors()
    await ctrl.note_box_disconnect()
    await ctrl.observe_box_frame({}, None, "")
    await ctrl.maybe_start_next()
    await ctrl.publish_setting_event_state(
        tbl_name="tbl_box_prms",
        tbl_item="MODE",
        new_value=1,
        device_id="DEV1",
        source="test",
    )
    await ctrl.on_box_setting_ack(tx_id="tx-1", ack=True)
