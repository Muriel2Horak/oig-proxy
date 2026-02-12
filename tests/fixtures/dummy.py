"""Dummy test fixtures for unit tests."""

import asyncio
from collections import deque
from typing import Iterable, Sequence


class DummyWriter:
    """Dummy writer for testing."""

    def __init__(self, side_effects: Sequence[BaseException] | None = None):
        self.data = []
        self._closing = False
        self._side_effects = deque(side_effects or [])

    def is_closing(self):
        return self._closing

    def get_extra_info(self, name):
        if name == "peername":
            return ("1.2.3.4", 1234)
        if name == "socket":
            return None
        return None

    def write(self, data):
        if self._side_effects:
            effect = self._side_effects.popleft()
            if isinstance(effect, BaseException):
                raise effect
        self.data.append(data)

    async def drain(self):
        return None

    def close(self):
        self._closing = True

    async def wait_closed(self):
        return None


class DummyReader:
    """Dummy reader for testing."""

    def __init__(self, payloads: Iterable | bytes | bytearray | memoryview | None = ()):
        if isinstance(payloads, (bytes, bytearray, memoryview)) or payloads is None:
            normalized = b"" if payloads is None else bytes(payloads)
            self._payloads = deque([normalized])
        else:
            self._payloads = deque(payloads)

    async def read(self, _size: int = -1):
        if not self._payloads:
            return b""
        item = self._payloads.popleft()
        if isinstance(item, BaseException):
            raise item
        if item is None:
            return b""
        if isinstance(item, (bytes, bytearray, memoryview)):
            return bytes(item)
        return bytes(item)

    def get_extra_info(self, name):
        if name == "peername":
            return ("1.2.3.4", 1234)
        if name == "socket":
            return None
        return None


class DummyQueue:
    """Dummy queue for testing."""

    def __init__(self):
        self.added = []

    async def add(self, frame_bytes, table_name, device_id):
        self.added.append((frame_bytes, table_name, device_id))

    def size(self):
        return len(self.added)
