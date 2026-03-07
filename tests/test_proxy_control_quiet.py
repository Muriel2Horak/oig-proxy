import importlib

import pytest

ControlPipeline = importlib.import_module("control_pipeline").ControlPipeline


@pytest.mark.asyncio
async def test_maybe_start_next_remains_noop_with_empty_state():
    pipe = ControlPipeline(object())
    await pipe.maybe_start_next()
    assert pipe.inflight is None
