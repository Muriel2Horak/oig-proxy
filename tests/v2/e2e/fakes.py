"""Explicit loopback and in-process fakes for local-control E2E tests."""
from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any, Callable

from capture.frame_capture import FrameCapture
from mqtt.client import MQTTMessageCallback
from protocol.frame import build_frame
from proxy.server import ProxyServer
from sensor.processor import FrameProcessor
from settings_constraints import CONTROL_WRITE_WHITELIST
from telemetry.settings_audit import SettingsAuditPublisher, SettingsAuditRecord
from twin.delivery import TwinCoordinator
from twin.handler import TwinControlHandler
from twin.state import CommandState, ConfirmedSetting, ControlPolicy, TwinCommand
from twin.store import TwinCommandStore


FRAME_END = b"</Frame>\r\n"


@dataclass(frozen=True, slots=True)
class PublishedMessage:
    """One immutable fake MQTT publication."""

    topic: str
    payload: bytes | str
    qos: int
    retain: bool


class FakeMQTTTransport:
    """In-process MQTT boundary with exact subscriptions and publications."""

    def __init__(self, *, namespace: str = "oig_local") -> None:
        self.ready = True
        self.namespace = namespace
        self.subscriptions: dict[str, MQTTMessageCallback] = {}
        self.published: list[PublishedMessage] = []

    def is_ready(self) -> bool:
        return self.ready

    def subscribe(self, topic: str, callback: MQTTMessageCallback) -> bool:
        self.subscriptions[topic] = callback
        return True

    def unsubscribe(self, topic: str) -> bool:
        self.subscriptions.pop(topic, None)
        return True

    @property
    def registered_subscriptions(self) -> frozenset[str]:
        return frozenset(self.subscriptions)

    def emit(self, topic: str, payload: bytes, *, retain: bool = False) -> None:
        for subscription, callback in tuple(self.subscriptions.items()):
            if self._matches(subscription, topic):
                callback(topic, bytes(payload), retain)
                return
        raise LookupError(f"no fake MQTT subscription matches {topic}")

    def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: int,
        retain: bool,
    ) -> bool:
        self.published.append(PublishedMessage(topic, payload, qos, retain))
        return True

    def publish_state(
        self,
        device_id: str,
        table: str,
        data: dict[str, Any],
    ) -> bool:
        return self.publish(
            f"{self.namespace}/{device_id}/{table}/state",
            json.dumps(data, sort_keys=True),
            qos=1,
            retain=True,
        )

    def send_discovery(self, **_kwargs: Any) -> bool:
        return True

    def publish_all_control_tombstones(self, device_id: str) -> None:
        for table in sorted(CONTROL_WRITE_WHITELIST):
            for key in sorted(CONTROL_WRITE_WHITELIST[table]):
                unique_id = f"{self.namespace}_{device_id}_{table}_{key}_cfg".lower()
                for component in ("number", "select", "switch"):
                    self.publish(
                        f"homeassistant/{component}/{unique_id}/config",
                        b"",
                        qos=1,
                        retain=True,
                    )

    @staticmethod
    def _matches(subscription: str, topic: str) -> bool:
        if subscription == topic:
            return True
        if subscription.endswith("/#"):
            return topic.startswith(subscription[:-1])
        return False


@dataclass(slots=True)
class LoopbackConnection:
    """One accepted or initiated loopback stream pair."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    async def read_frame(self, *, timeout: float = 2.0) -> bytes:
        return await asyncio.wait_for(self.reader.readuntil(FRAME_END), timeout)

    async def read_bytes(self, count: int, *, timeout: float = 2.0) -> bytes:
        return await asyncio.wait_for(self.reader.readexactly(count), timeout)

    async def send(self, raw: bytes) -> None:
        self.writer.write(raw)
        await self.writer.drain()

    async def send_chunks(self, chunks: tuple[bytes, ...]) -> None:
        for chunk in chunks:
            self.writer.write(chunk)
            await self.writer.drain()

    async def close(self) -> None:
        if not self.writer.is_closing():
            self.writer.close()
            await self.writer.wait_closed()


class FakeCloudEndpoint:
    """Numeric-loopback cloud listener supporting repeated proxy sessions."""

    def __init__(self) -> None:
        self._server: asyncio.Server | None = None
        self._accepted: asyncio.Queue[LoopbackConnection] = asyncio.Queue()
        self.connections: list[LoopbackConnection] = []
        self.current: LoopbackConnection | None = None

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("fake cloud is not listening")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._accept,
            "127.0.0.1",
            0,
        )

    async def _accept(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        connection = LoopbackConnection(reader, writer)
        self.connections.append(connection)
        await self._accepted.put(connection)

    async def wait_connected(self) -> LoopbackConnection:
        self.current = await asyncio.wait_for(self._accepted.get(), 2.0)
        return self.current

    async def read_frame(self) -> bytes:
        if self.current is None:
            raise RuntimeError("fake cloud has no proxy connection")
        return await self.current.read_frame()

    async def read_bytes(self, count: int) -> bytes:
        if self.current is None:
            raise RuntimeError("fake cloud has no proxy connection")
        return await self.current.read_bytes(count)

    async def send(self, raw: bytes) -> None:
        if self.current is None:
            raise RuntimeError("fake cloud has no proxy connection")
        await self.current.send(raw)

    async def close_current(self) -> None:
        if self.current is not None:
            await self.current.close()
            self.current = None

    async def stop(self) -> None:
        for connection in reversed(self.connections):
            await connection.close()
        self.connections.clear()
        self.current = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


class FakeBoxEndpoint:
    """Actual numeric-loopback BOX client connected to the proxy listener."""

    def __init__(self) -> None:
        self.connection: LoopbackConnection | None = None

    async def connect(self, port: int) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        self.connection = LoopbackConnection(reader, writer)

    async def send(self, raw: bytes) -> None:
        if self.connection is None:
            raise RuntimeError("fake BOX is disconnected")
        await self.connection.send(raw)

    async def send_chunks(self, chunks: tuple[bytes, ...]) -> None:
        if self.connection is None:
            raise RuntimeError("fake BOX is disconnected")
        await self.connection.send_chunks(chunks)

    async def read_frame(self) -> bytes:
        if self.connection is None:
            raise RuntimeError("fake BOX is disconnected")
        return await self.connection.read_frame()

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None


class _FakeSensorMap:
    def iter_sensors(self) -> tuple[()]:
        return ()

    def lookup(self, _table: str, _key: str) -> None:
        return None


@dataclass(slots=True)
class LocalControlHarness:
    """Real proxy/store/coordinator around explicit fake external boundaries."""

    root: Path
    validate_config: Callable[[Any], None]
    max_attempts: int = 8
    control_enabled: bool = True
    known_identity: bool = True
    proxy_mode: str = "online"
    db_path: Path = field(init=False)
    capture_path: Path = field(init=False)
    fake_mqtt: FakeMQTTTransport = field(init=False)
    fake_cloud: FakeCloudEndpoint = field(init=False)
    fake_box: FakeBoxEndpoint = field(init=False)
    store: TwinCommandStore = field(init=False)
    coordinator: TwinCoordinator = field(init=False)
    handler: TwinControlHandler | None = field(init=False, default=None)
    proxy: ProxyServer = field(init=False)
    capture: FrameCapture = field(init=False)
    audit_records: list[SettingsAuditRecord] = field(init=False, default_factory=list)
    bound_device_id: str | None = field(init=False, default=None)
    config: SimpleNamespace = field(init=False)
    _publisher: SettingsAuditPublisher = field(init=False)
    _processor: FrameProcessor = field(init=False)
    _loop: asyncio.AbstractEventLoop = field(init=False)
    _clock_ms: int = field(init=False)

    def __post_init__(self) -> None:
        self.db_path = self.root / "twin.db"
        self.capture_path = self.root / "capture.db"
        self.fake_mqtt = FakeMQTTTransport()
        self.fake_cloud = FakeCloudEndpoint()
        self.fake_box = FakeBoxEndpoint()
        self._clock_ms = self.now_ms()

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.fake_cloud.start()
        self.config = self._make_config(self.fake_cloud.port)
        self.validate_config(self.config)
        self.capture = FrameCapture(
            db_path=str(self.capture_path),
            capture_raw_bytes=True,
            retention_days=1,
        )
        self.capture.start()
        self._open_store()
        if self.known_identity:
            self.bound_device_id = "123"
            self.store.observe_device(
                device_id="123",
                observed_at_ms=1,
                observed_wire_id=14_000_000,
                observed_wire_id_set=1_786_000_000,
            )
        self._compose_runtime()
        if not self.control_enabled and self.bound_device_id is not None:
            self.fake_mqtt.publish_all_control_tombstones(self.bound_device_id)
        await self._start_handler_if_eligible()
        await self.proxy.start()
        proxy_server = self.proxy._server  # pylint: disable=protected-access
        if proxy_server is None or not proxy_server.sockets:
            raise RuntimeError("proxy did not bind a loopback socket")
        proxy_port = int(proxy_server.sockets[0].getsockname()[1])
        await self.fake_box.connect(proxy_port)
        if self.proxy_mode != "offline":
            await self.fake_cloud.wait_connected()

    def _make_config(self, cloud_port: int) -> SimpleNamespace:
        return SimpleNamespace(
            proxy_host="127.0.0.1",
            proxy_port=0,
            cloud_host="127.0.0.1",
            cloud_port=cloud_port,
            cloud_connect_timeout=1.0,
            cloud_dialog_timeout_s=1.0,
            control_ack_timeout_s=1.0,
            mqtt_host="127.0.0.1",
            mqtt_namespace="oig_local",
            dns_upstream="127.0.0.1",
            telemetry_mqtt_broker="127.0.0.1:1883",
            telemetry_enabled=False,
            twin_db_path=str(self.db_path),
            proxy_mode=self.proxy_mode,
            hybrid_fail_threshold=1,
            hybrid_retry_interval=0.0,
            max_concurrent_connections=8,
            local_getactual_enabled=False,
            local_getactual_interval_s=10,
        )

    def _open_store(self) -> None:
        self.store = TwinCommandStore(
            self.db_path,
            policy=ControlPolicy(
                ack_timeout_ms=1_000,
                event_timeout_ms=5_000,
                pending_ttl_ms=60_000,
                max_attempts=self.max_attempts,
            ),
        )
        now_ms = self.now_ms()
        self.store.open(now_ms=now_ms)
        self.store.recover(now_ms=now_ms)

    def _compose_runtime(self) -> None:
        self._publisher = SettingsAuditPublisher(
            self.audit_records.append,
            acceptance_ledger=self.store,
        )
        self.coordinator = TwinCoordinator(
            self.store,
            control_enabled=self.control_enabled,
            audit_publisher=self._publisher,
            clock_ms=self.clock_ms,
        )
        self._processor = FrameProcessor(
            self.fake_mqtt,  # type: ignore[arg-type]
            _FakeSensorMap(),  # type: ignore[arg-type]
        )

        async def valid_device(
            device_id: str,
            observed_id: int | None,
            observed_id_set: int | None,
        ) -> bool:
            if (
                not device_id
                or observed_id is None
                or observed_id_set is None
                or (self.bound_device_id is not None and device_id != self.bound_device_id)
            ):
                return False
            self.bound_device_id = device_id
            self.store.observe_device(
                device_id=device_id,
                observed_at_ms=self.now_ms(),
                observed_wire_id=observed_id,
                observed_wire_id_set=observed_id_set,
            )
            await self._start_handler_if_eligible()
            return True

        async def confirmed(confirmation: ConfirmedSetting) -> None:
            self._processor.publish_confirmed_setting(confirmation)

        self.proxy = ProxyServer(
            self.config,
            twin_coordinator=self.coordinator,
            on_valid_device=valid_device,
            on_committed_confirmation=confirmed,
            frame_capture=self.capture,
            clock_ms=self.clock_ms,
        )

    async def _start_handler_if_eligible(self) -> None:
        if (
            self.handler is not None
            or not self.control_enabled
            or self.bound_device_id is None
        ):
            return
        self.store.read_device(self.bound_device_id)
        handler = TwinControlHandler(
            mqtt=self.fake_mqtt,  # type: ignore[arg-type]
            store=self.store,
            device_id=self.bound_device_id,
            control_enabled=True,
            loop=self._loop,
            namespace=self.fake_mqtt.namespace,
            audit_publisher=self._publisher,
        )
        if await handler.start():
            self.handler = handler

    async def enqueue(self, table: str, key: str, value: str) -> TwinCommand:
        if self.bound_device_id is None:
            raise RuntimeError("cannot enqueue before exact identity binding")
        before = len(self.command_ids())
        topic = f"oig/{self.bound_device_id}/control/set"
        self.fake_mqtt.emit(
            topic,
            json.dumps({"table": table, "key": key, "value": value}).encode(),
        )
        await self.wait_until(lambda: len(self.command_ids()) == before + 1)
        return self.store.read_command(self.command_ids()[-1])

    async def restart_proxy_and_store(self) -> None:
        await self.fake_box.close()
        await self.proxy.stop()
        if self.handler is not None:
            await self.handler.stop()
            self.handler = None
        self.store.close()
        await self.fake_cloud.close_current()
        self._open_store()
        self._compose_runtime()
        await self._start_handler_if_eligible()
        await self.proxy.start()
        proxy_server = self.proxy._server  # pylint: disable=protected-access
        if proxy_server is None or not proxy_server.sockets:
            raise RuntimeError("restarted proxy did not bind")
        await self.fake_box.connect(int(proxy_server.sockets[0].getsockname()[1]))
        if self.proxy_mode != "offline":
            await self.fake_cloud.wait_connected()

    async def stop(self) -> None:
        await self.fake_box.close()
        await self.proxy.stop()
        if self.handler is not None:
            await self.handler.stop()
            self.handler = None
        self.store.close()
        self.capture.stop()
        await self.fake_cloud.stop()

    async def begin_local_delivery(
        self,
        table: str = "tbl_box_prms",
        key: str = "MODE",
        value: str = "2",
    ) -> tuple[TwinCommand, bytes, bytes]:
        command = await self.enqueue(table, key, value)
        poll = self.isnewset_poll()
        await self.fake_box.send(poll)
        assert await self.fake_cloud.read_frame() == poll
        end = self.cloud_end()
        await self.fake_cloud.send(end)
        setting = await self.fake_box.read_frame()
        return command, setting, end

    async def deliver_and_ack(
        self,
        table: str = "tbl_box_prms",
        key: str = "MODE",
        value: str = "2",
    ) -> TwinCommand:
        command, _setting, end = await self.begin_local_delivery(table, key, value)
        await self.fake_box.send(self.setting_ack())
        assert await self.fake_box.read_frame() == end
        return self.store.read_command(command.command_id)

    def isnewset_poll(
        self,
        *,
        device_id: str = "123",
        message_id: int = 14_000_000,
        id_set: int = 1_786_000_000,
    ) -> bytes:
        return self.frame(
            result="IsNewSet",
            device_id=device_id,
            message_id=message_id,
            id_set=id_set,
        )

    def setting_ack(
        self,
        *,
        result: str = "ACK",
        reason: str = "Setting",
        rdt: str = "2026-08-06 10:12:02",
    ) -> bytes:
        return self.frame(
            result=result,
            reason=reason,
            extra=f"<Rdt>{rdt}</Rdt>",
        )

    def cloud_end(self, *, marker: str = "exact-end") -> bytes:
        return self.frame(result="END", extra=f"<Marker>{marker}</Marker>")

    def cloud_setting(self, table: str, key: str, value: str) -> bytes:
        return self.frame(
            result="Setting",
            device_id="123",
            table=table,
            key=key,
            value=value,
            message_id=14_000_001,
            id_set=1_786_000_001,
        )

    def setting_event(
        self,
        command: TwinCommand,
        *,
        device_id: str | None = None,
        table: str | None = None,
        key: str | None = None,
        value: str | None = None,
        event_id_set: int = 1_786_000_100,
    ) -> bytes:
        actual_value = command.value_text if value is None else value
        return self.frame(
            device_id=command.device_id if device_id is None else device_id,
            table="tbl_events",
            id_set=event_id_set,
            extra=(
                "<DT>2026-08-06 10:13:00</DT><Type>Setting</Type>"
                f"<Content>Remotely : {table or command.table_name} / "
                f"{key or command.item_name}: [0]-&gt;[{actual_value}]</Content>"
            ),
        )

    def sensor_frame(self, marker: str) -> bytes:
        return self.frame(
            device_id="123",
            table="tbl_actual",
            extra=f"<Marker>{marker}</Marker>",
        )

    @staticmethod
    def frame(
        *,
        result: str | None = None,
        device_id: str | None = None,
        table: str | None = None,
        key: str | None = None,
        value: str | None = None,
        reason: str | None = None,
        message_id: int | None = None,
        id_set: int | None = None,
        extra: str = "",
    ) -> bytes:
        tags: list[str] = []
        if result is not None:
            tags.append(f"<Result>{result}</Result>")
        if message_id is not None:
            tags.append(f"<ID>{message_id}</ID>")
        if device_id is not None:
            tags.append(f"<ID_Device>{device_id}</ID_Device>")
        if id_set is not None:
            tags.append(f"<ID_Set>{id_set}</ID_Set>")
        if value is not None:
            tags.append(f"<NewValue>{value}</NewValue>")
        if reason is not None:
            tags.append(f"<Reason>{reason}</Reason>")
        if table is not None:
            tags.append(f"<TblName>{table}</TblName>")
        if key is not None:
            tags.append(f"<TblItem>{key}</TblItem>")
        tags.append(extra)
        return build_frame("".join(tags)).encode("utf-8")

    def command_ids(self) -> list[str]:
        with closing(sqlite3.connect(self.db_path)) as connection:
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT command_id FROM commands ORDER BY created_at_ms, command_id"
                )
            ]

    def command_states(self) -> list[tuple[str, CommandState]]:
        with closing(sqlite3.connect(self.db_path)) as connection:
            return [
                (str(row[0]), CommandState(str(row[1])))
                for row in connection.execute(
                    "SELECT value_text, state FROM commands ORDER BY created_at_ms, command_id"
                )
            ]

    def confirmed_state_messages(self) -> list[PublishedMessage]:
        return [
            message
            for message in self.fake_mqtt.published
            if message.topic.endswith("/state") and "/tbl_" in message.topic
        ]

    @staticmethod
    async def wait_until(predicate: Callable[[], bool]) -> None:
        async def wait() -> None:
            while not predicate():
                await asyncio.sleep(0)

        await asyncio.wait_for(wait(), 2.0)

    @staticmethod
    def now_ms() -> int:
        import time

        return time.time_ns() // 1_000_000

    def clock_ms(self) -> int:
        return max(self._clock_ms, self.now_ms())

    def advance_clock(self, milliseconds: int) -> None:
        self._clock_ms = max(self._clock_ms, self.now_ms()) + milliseconds


def corrupt_crc(frame: bytes) -> bytes:
    """Return the same frame with an invalid exact CRC field."""
    marker = frame.rfind(b"<CRC>") + len(b"<CRC>")
    return frame[:marker] + b"00000" + frame[marker + 5:]
