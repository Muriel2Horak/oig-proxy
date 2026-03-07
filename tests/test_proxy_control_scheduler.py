import importlib

import pytest

ControlPipeline = importlib.import_module("control_pipeline").ControlPipeline


@pytest.mark.asyncio
async def test_maybe_start_next_does_not_raise_when_inflight_exists():
    pipe = ControlPipeline(object())
    pipe.inflight = {"tx_id": "inflight"}
    await pipe.maybe_start_next()
    assert pipe.inflight == {"tx_id": "inflight"}
