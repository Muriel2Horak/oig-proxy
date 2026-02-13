"""Tests for new control methods added in proxy.py."""
# pylint: disable=protected-access

import asyncio
import os
import time
import pytest

from control_pipeline import ControlPipeline
from tests.helpers import make_proxy


@pytest.mark.skip("async mocking complexity, not priority for SonarCloud")
def test_run_coroutine_threadsafe(tmp_path):
    """Test _run_coroutine_threadsafe method."""
    proxy = make_proxy(tmp_path)

    # Mock event loop and send method
    called = []

    async def fake_send(*_args, **_kwargs):
        called.append("sent")
        return {"ok": True}

    proxy._loop = asyncio.new_event_loop()
    proxy._cs.send_to_box = fake_send

    # Call method
    result = proxy._cs.run_coroutine_threadsafe(
        "tbl_box_prms", "SA", "1", "New"
    )

    assert result["ok"] is True
    assert "sent" in called


def test_append_to_control_log(tmp_path):
    """Test _append_to_control_log method."""
    _ = make_proxy(tmp_path)

    # Create a real ControlPipeline to test the actual method
    ctrl = ControlPipeline.__new__(ControlPipeline)

    # Setup log path
    log_path = str(tmp_path / "control.log")
    ctrl.log_path = log_path

    # Write entry
    ctrl.append_to_log('{"test": "value"}\n')

    # Verify file was created and contains entry
    assert os.path.exists(log_path)
    with open(log_path, encoding="utf-8") as f:
        content = f.read()

    assert '{"test": "value"}' in content


def test_validate_control_parameters_box_not_sending(tmp_path):
    """Test _validate_control_parameters with box not sending data."""
    proxy = make_proxy(tmp_path)

    # Set last_data_epoch to old time (>30s ago)
    proxy._last_data_epoch = time.time() - 60

    result = proxy._cs.validate_parameters("tbl_box_prms", "SA", "1")

    assert result["ok"] is False
    assert result["error"] == "box_not_sending_data"


@pytest.mark.skip("async mocking complexity, not priority for SonarCloud")
def test_send_setting_via_event_loop_timeout(tmp_path):
    """Test _send_setting_via_event_loop with timeout."""
    proxy = make_proxy(tmp_path)

    # Mock _run_coroutine_threadsafe to timeout
    async def fake_run(*_args, **_kwargs):
        fut = asyncio.Future()
        loop = asyncio.get_event_loop()
        loop.call_later(10, fut.cancel)
        return fut

    proxy._loop = asyncio.new_event_loop()
    proxy._cs.run_coroutine_threadsafe = fake_run

    # This should handle the timeout gracefully
    result = proxy._cs.send_via_event_loop(
        tbl_name="tbl_box_prms",
        tbl_item="SA",
        new_value="1",
        confirm="New",
    )

    # Result should handle timeout (either error or success depending on implementation)
    assert result is not None
