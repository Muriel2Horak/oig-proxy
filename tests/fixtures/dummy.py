"""Dummy test fixtures for unit tests."""

import asyncio


class DummyWriter:
    """Dummy writer for testing."""

    def __init__(self):
        self.data = []
        self._closing = False

    def is_closing(self):
        return self._closing

    def get_extra_info(self, name):
        return None

    def write(self, data):
        self.data.append(data)

    async def drain(self):
        return None

    def close(self):
        self._closing = True

    async def wait_closed(self):
        return None


class DummyReader:
    """Dummy reader for testing."""

    def __init__(self, payloads: list[bytes]):
        self._payloads = payloads

    async def read(self, _size):
        if not self._payloads:
            return b""
        item = self._payloads.pop(0)
        if item == b"" or item == "":
            return None
        return item

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
