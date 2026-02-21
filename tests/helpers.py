"""Shared test helper functions."""

import time
import asyncio
from collections import deque
from unittest.mock import MagicMock

# pylint: disable=protected-access
import proxy as proxy_module
from control_settings import ControlSettings
from mode_persistence import ModePersistence
from proxy_status import ProxyStatusReporter
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

    mp = ModePersistence.__new__(ModePersistence)
    mp._proxy = proxy
    mp.mode_value = None
    mp.mode_device_id = None
    mp.mode_pending_publish = False
    mp.prms_tables = {}
    mp.prms_pending_publish = False
    mp.prms_device_id = None
    proxy._mp = mp

    cs = ControlSettings.__new__(ControlSettings)
    cs._proxy = proxy
    cs.pending = None
    cs.pending_frame = None
    cs.set_commands_buffer = []
    proxy._cs = cs

    ps = ProxyStatusReporter.__new__(ProxyStatusReporter)
    ps._proxy = proxy
    ps.mqtt_was_ready = False
    ps.last_hb_ts = 0.0
    ps.hb_interval_s = 0.0
    ps.status_attrs_topic = "oig/status/attrs"
    proxy._ps = ps

    return proxy
