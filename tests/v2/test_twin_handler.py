"""Exact-device, retain-aware durable MQTT control ingress tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from twin.handler import TwinControlHandler
from twin.state import CommandState, IngressDisposition
from twin.store import StoreRecordNotFound, TwinCommandStore


@dataclass
class FakeMQTT:
    """Minimal synchronous transport boundary used by the handler."""

    ready: bool = True
    subscribe_results: list[bool] = field(default_factory=list)
    subscriptions: list[tuple[str, Callable[..., None]]] = field(default_factory=list)
    unsubscriptions: list[str] = field(default_factory=list)

    def is_ready(self) -> bool:
        return self.ready

    def subscribe(self, topic: str, callback: Callable[..., None]) -> bool:
        result = self.subscribe_results.pop(0) if self.subscribe_results else True
        if result:
            self.subscriptions.append((topic, callback))
        return result

    def unsubscribe(self, topic: str) -> bool:
        self.unsubscriptions.append(topic)
        self.subscriptions = [
            item for item in self.subscriptions if item[0] != topic
        ]
        return True


def _learn_device(store: TwinCommandStore) -> None:
    store.observe_device(
        device_id="123",
        observed_at_ms=90,
        observed_wire_id=14_000_000,
        observed_wire_id_set=1_786_000_000,
    )


def _handler(
    store: TwinCommandStore,
    mqtt: FakeMQTT,
    *,
    device_id: str = "123",
    enabled: bool = True,
    proxy_control: Callable[[str, str, str], bool] | None = None,
    publisher: Any | None = None,
) -> TwinControlHandler:
    return TwinControlHandler(
        mqtt=mqtt,
        store=store,
        device_id=device_id,
        control_enabled=enabled,
        loop=asyncio.get_running_loop(),
        proxy_control_handler=proxy_control,
        audit_publisher=publisher,
    )


@pytest.mark.asyncio
async def test_start_subscribes_only_exact_device_topics(
    store: TwinCommandStore,
) -> None:
    mqtt = FakeMQTT()
    handler = _handler(store, mqtt)

    assert await handler.start() is True
    assert [topic for topic, _callback in mqtt.subscriptions] == [
        "oig/123/control/set",
        "oig_local/123/set/#",
    ]


@pytest.mark.asyncio
async def test_start_rolls_back_first_subscription_when_second_fails(
    store: TwinCommandStore,
) -> None:
    mqtt = FakeMQTT(subscribe_results=[True, False])
    handler = _handler(store, mqtt)

    assert await handler.start() is False
    assert mqtt.unsubscriptions == ["oig/123/control/set"]
    assert mqtt.subscriptions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("device_id", ("", "bad/device", "bad+", "bad#", "bad\0"))
async def test_unknown_or_unsafe_device_refuses_subscription_and_audits(
    store: TwinCommandStore,
    device_id: str,
) -> None:
    mqtt = FakeMQTT()
    handler = _handler(store, mqtt, device_id=device_id)

    assert await handler.start() is False
    assert mqtt.subscriptions == []
    await handler.handle_message(
        "oig/123/control/set",
        b"{}",
        False,
        received_at_ms=100,
    )
    assert store.read_latest_ingress().disposition is IngressDisposition.REJECTED_UNKNOWN_DEVICE


@pytest.mark.asyncio
async def test_disabled_handler_never_subscribes_and_rejects_before_parse(
    store: TwinCommandStore,
) -> None:
    mqtt = FakeMQTT()
    handler = _handler(store, mqtt, enabled=False)

    assert await handler.start() is False
    await handler.handle_message(
        "oig/123/control/set",
        b"not-json",
        False,
        received_at_ms=100,
    )
    assert mqtt.subscriptions == []
    assert store.read_latest_ingress().disposition is IngressDisposition.REJECTED_DISABLED


@pytest.mark.asyncio
async def test_retained_message_is_rejected_before_json_and_enqueue(
    store: TwinCommandStore,
) -> None:
    _learn_device(store)
    handler = _handler(store, FakeMQTT())

    await handler.handle_message(
        "oig/123/control/set",
        b"not-json",
        True,
        received_at_ms=100,
    )

    ingress = store.read_latest_ingress()
    assert ingress.disposition is IngressDisposition.REJECTED_RETAINED
    assert ingress.command_id is None and ingress.audit_id is None
    assert store.status_snapshot("123").nonterminal_commands == 0


@pytest.mark.asyncio
async def test_exact_json_command_links_ingress_command_and_audit(
    store: TwinCommandStore,
) -> None:
    _learn_device(store)
    publisher = MagicMock()
    publisher.publish_committed_async = AsyncMock()
    handler = _handler(store, FakeMQTT(), publisher=publisher)

    await handler.handle_message(
        "oig/123/control/set",
        b'{"device_id":"123","table":"tbl_box_prms","key":"MODE","value":2}',
        False,
        received_at_ms=100,
    )

    command = store.single_nonterminal("123")
    ingress = store.read_latest_ingress()
    assert (
        command.device_id,
        command.table_name,
        command.item_name,
        command.value_text,
        command.state,
    ) == ("123", "tbl_box_prms", "MODE", "2", CommandState.PENDING)
    assert (ingress.command_id, ingress.audit_id) == (
        command.command_id,
        command.audit_id,
    )
    publisher.publish_committed_async.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("topic", "payload", "expected"),
    (
        (
            "oig/999/control/set",
            b'{"table":"tbl_box_prms","key":"MODE","value":2}',
            IngressDisposition.REJECTED_DEVICE_MISMATCH,
        ),
        (
            "oig/123/control/set/extra",
            b"{}",
            IngressDisposition.REJECTED_TOPIC,
        ),
        (
            "oig/123/control/set",
            b'{"device_id":"999","table":"tbl_box_prms","key":"MODE","value":2}',
            IngressDisposition.REJECTED_DEVICE_MISMATCH,
        ),
        (
            "oig_local/123/set/tbl_box_prms/MODE/extra",
            b"2",
            IngressDisposition.REJECTED_TOPIC,
        ),
    ),
)
async def test_topic_and_payload_device_mismatches_are_audited(
    store: TwinCommandStore,
    topic: str,
    payload: bytes,
    expected: IngressDisposition,
) -> None:
    _learn_device(store)
    handler = _handler(store, FakeMQTT())

    await handler.handle_message(
        topic,
        payload,
        False,
        received_at_ms=100,
    )

    assert store.read_latest_ingress().disposition is expected
    assert store.status_snapshot("123").nonterminal_commands == 0


@pytest.mark.asyncio
async def test_exact_five_segment_compatibility_topic_enqueues(
    store: TwinCommandStore,
) -> None:
    _learn_device(store)
    handler = _handler(store, FakeMQTT())

    await handler.handle_message(
        "oig_local/123/set/tbl_box_prms/MODE",
        b" 2 ",
        False,
        received_at_ms=100,
    )

    assert store.single_nonterminal("123").value_text == "2"
    assert store.read_latest_ingress().disposition is IngressDisposition.ACCEPTED_COMMAND


@pytest.mark.asyncio
async def test_payload_size_boundary_accepts_and_one_byte_over_rejects(
    store: TwinCommandStore,
) -> None:
    _learn_device(store)
    handler = _handler(store, FakeMQTT())
    base = b'{"table":"tbl_box_prms","key":"MODE","value":2}'
    exact = base + b" " * (16_384 - len(base))

    await handler.handle_message(
        "oig/123/control/set",
        exact,
        False,
        received_at_ms=100,
    )
    assert store.read_latest_ingress().disposition is IngressDisposition.ACCEPTED_COMMAND

    await handler.handle_message(
        "oig/123/control/set",
        exact + b"x",
        False,
        received_at_ms=101,
    )
    assert store.read_latest_ingress().disposition is IngressDisposition.REJECTED_OVERSIZE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        (b"\xff", IngressDisposition.REJECTED_UTF8),
        (b"{", IngressDisposition.REJECTED_JSON),
        (b'{"table":"tbl_box_prms","key":"MODE","value":NaN}', IngressDisposition.REJECTED_JSON),
        (b'[]', IngressDisposition.REJECTED_SCHEMA),
        (b'{"table":1,"key":"MODE","value":2}', IngressDisposition.REJECTED_SCHEMA),
        (b'{"table":"tbl_box_prms","key":"UNKNOWN","value":2}', IngressDisposition.REJECTED_NOT_ALLOWED),
        (b'{"table":"tbl_box_prms","key":"MODE","value":2.5}', IngressDisposition.REJECTED_VALUE),
    ),
)
async def test_bounded_validation_rejections(
    store: TwinCommandStore,
    payload: bytes,
    expected: IngressDisposition,
) -> None:
    _learn_device(store)
    handler = _handler(store, FakeMQTT())

    await handler.handle_message(
        "oig/123/control/set",
        payload,
        False,
        received_at_ms=100,
    )

    assert store.read_latest_ingress().disposition is expected
    assert store.status_snapshot("123").nonterminal_commands == 0


@pytest.mark.asyncio
async def test_forbidden_xml_text_is_rejected_before_enqueue(
    store: TwinCommandStore,
) -> None:
    _learn_device(store)
    handler = _handler(store, FakeMQTT())

    await handler.handle_message(
        "oig_local/123/set/tbl_box_prms/MODE",
        b"2\x01",
        False,
        received_at_ms=100,
    )

    assert store.read_latest_ingress().disposition is IngressDisposition.REJECTED_XML


@pytest.mark.asyncio
async def test_proxy_control_is_audited_before_dispatch_without_command_ids(
    store: TwinCommandStore,
) -> None:
    _learn_device(store)
    dispatched: list[tuple[str, str, str]] = []

    def proxy_control(table: str, key: str, value: str) -> bool:
        ingress = store.read_latest_ingress()
        assert ingress.disposition is IngressDisposition.ACCEPTED_PROXY_CONTROL
        assert ingress.command_id is None and ingress.audit_id is None
        dispatched.append((table, key, value))
        return True

    handler = _handler(
        store,
        FakeMQTT(),
        proxy_control=proxy_control,
    )
    await handler.handle_message(
        "oig/123/control/set",
        b'{"table":"proxy_control","key":"PROXY_MODE","value":2}',
        False,
        received_at_ms=100,
    )

    assert dispatched == [("proxy_control", "PROXY_MODE", "2")]
    assert store.status_snapshot("123").nonterminal_commands == 0


@pytest.mark.asyncio
async def test_retained_proxy_control_never_dispatches(
    store: TwinCommandStore,
) -> None:
    dispatched = MagicMock()
    handler = _handler(store, FakeMQTT(), proxy_control=dispatched)

    await handler.handle_message(
        "oig/123/control/set",
        b'{"table":"proxy_control","key":"PROXY_MODE","value":2}',
        True,
        received_at_ms=100,
    )

    dispatched.assert_not_called()
    assert store.read_latest_ingress().disposition is IngressDisposition.REJECTED_RETAINED


@pytest.mark.asyncio
async def test_paho_callback_schedules_sqlite_work_on_application_loop(
    store: TwinCommandStore,
) -> None:
    _learn_device(store)
    mqtt = FakeMQTT()
    handler = _handler(store, mqtt)
    assert await handler.start()
    callback = mqtt.subscriptions[0][1]

    callback(
        "oig/123/control/set",
        b'{"table":"tbl_box_prms","key":"MODE","value":2}',
        False,
    )
    await asyncio.sleep(0)
    await handler.stop()

    assert store.single_nonterminal("123").state is CommandState.PENDING
    assert mqtt.unsubscriptions == [
        "oig/123/control/set",
        "oig_local/123/set/#",
    ]


@pytest.mark.asyncio
async def test_unavailable_store_never_dispatches_proxy_control() -> None:
    store = MagicMock()
    store.record_proxy_control_ingress.side_effect = RuntimeError("unavailable")
    dispatched = MagicMock()
    handler = TwinControlHandler(
        mqtt=FakeMQTT(),
        store=store,
        device_id="123",
        control_enabled=True,
        loop=asyncio.get_running_loop(),
        proxy_control_handler=dispatched,
    )

    await handler.handle_message(
        "oig/123/control/set",
        b'{"table":"proxy_control","key":"PROXY_MODE","value":2}',
        False,
        received_at_ms=100,
    )

    dispatched.assert_not_called()
    assert handler.store_failure_count == 1


def test_store_fixture_has_no_unexpected_ingress(store: TwinCommandStore) -> None:
    with pytest.raises(StoreRecordNotFound):
        store.read_latest_ingress()
