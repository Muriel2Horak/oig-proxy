"""Tests for new control methods added in proxy.py."""

import asyncio
import os
import threading
import time

# pylint: disable=protected-access
import proxy as proxy_module
from models import ProxyMode


def make_proxy(tmp_path):
    """Create minimal proxy object for testing."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy.mode = ProxyMode.ONLINE
    proxy._last_data_epoch = time.time()
    proxy.box_connected = True
    proxy._loop = None
    return proxy


def test_run_coroutine_threadsafe(tmp_path):
    """Test _run_coroutine_threadsafe method."""
    proxy = make_proxy(tmp_path)

    # Mock event loop and send method
    called = []

    async def fake_send(*_args, **_kwargs):
        called.append("sent")
        return {"ok": True}

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    try:
        proxy._loop = loop
        proxy._send_setting_to_box = fake_send

        # Call method
        result = proxy._run_coroutine_threadsafe(
            "tbl_box_prms", "SA", "1", "New"
        )

        assert result["ok"] is True
        assert "sent" in called
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()
        proxy._loop = None


def test_append_to_control_log(tmp_path):
    """Test _append_to_control_log method."""
    proxy = make_proxy(tmp_path)

    # Setup log path
    log_path = str(tmp_path / "control.log")
    proxy._control_log_path = log_path

    # Write entry
    proxy._append_to_control_log('{"test": "value"}\n')

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

    result = proxy._validate_control_parameters("tbl_box_prms", "SA", "1")

    assert result["ok"] is False
    assert result["error"] == "box_not_sending_data"


def test_send_setting_via_event_loop_timeout(tmp_path):
    """Test _send_setting_via_event_loop with timeout."""
    proxy = make_proxy(tmp_path)

    proxy._run_coroutine_threadsafe = lambda *_args, **_kwargs: {  # noqa: E731
        "ok": False,
        "error": "timeout",
    }

    proxy._loop = asyncio.new_event_loop()
    loop = proxy._loop
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    try:
        # This should handle the timeout gracefully
        result = proxy._send_setting_via_event_loop(
            tbl_name="tbl_box_prms",
            tbl_item="SA",
            new_value="1",
            confirm="New",
        )
        assert result["ok"] is False
        assert result["error"] == "timeout"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()
