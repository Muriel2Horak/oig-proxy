import importlib

import pytest

ControlPipeline = importlib.import_module("control_pipeline").ControlPipeline


@pytest.mark.asyncio
async def test_on_box_setting_ack_is_safe_noop():
    pipe = ControlPipeline(object())
    await pipe.on_box_setting_ack(tx_id="tx-1", ack=True)
    await pipe.on_box_setting_ack(tx_id=None, ack=False)


@pytest.mark.asyncio
async def test_observe_box_frame_is_safe_noop():
    pipe = ControlPipeline(object())
    await pipe.observe_box_frame({"k": "v"}, "tbl_events", "<frame />")
    await pipe.observe_box_frame({}, None, "")


def test_coerce_value_variants():
    assert ControlPipeline.coerce_value(None) is None
    assert ControlPipeline.coerce_value(True) is True
    assert ControlPipeline.coerce_value("true") is True
    assert ControlPipeline.coerce_value("-3") == -3
    assert ControlPipeline.coerce_value("3.5") == 3.5
    assert ControlPipeline.coerce_value("abc") == "abc"
