"""Shared test helper functions."""

import time
import asyncio
from collections import deque
from unittest.mock import MagicMock

# pylint: disable=protected-access
import proxy as proxy_module
from control_settings import ControlSettings
from models import ProxyMode


def make_proxy(tmp_path):
    """Create minimal proxy object for testing."""
    proxy = proxy_module.OIGProxy.__new__(proxy_module.OIGProxy)
    proxy.device_id = "DEV1"
    proxy._hm = MagicMock()
    proxy._hm.mode = ProxyMode.ONLINE
    proxy._last_data_epoch = time.time()
    proxy.box_connected = True
    proxy._box_conn_lock = asyncio.Lock()
    proxy._tc = MagicMock()
    proxy._ctrl = MagicMock()
    proxy._msc = MagicMock()
    proxy._cf = MagicMock()
    proxy._loop = None
    proxy._active_box_writer = None
    proxy._active_box_peer = None
    proxy._background_tasks = set()

    cs = ControlSettings.__new__(ControlSettings)
    cs._proxy = proxy
    cs.pending = None
    cs.set_commands_buffer = []
    proxy._cs = cs

    return proxy
