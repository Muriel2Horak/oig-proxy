"""Dummy test fixtures for unit tests."""

import asyncio


class DummyWriter:
    """Dummy writer for testing."""

    def __init__(self):
        self.data = []
        self._closing = False

    def is_closing(self):
        return self._closing

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

    def __init__(self, payload: bytes):
        self._payload = payload

    async def read(self, _size):
        return self._payload


class DummyQueue:
    """Dummy queue for testing."""

    def __init__(self):
        self.added = []

    async def add(self, frame_bytes, table_name, device_id):
        self.added.append((frame_bytes, table_name, device_id))

    def size(self):
        return len(self.added)
