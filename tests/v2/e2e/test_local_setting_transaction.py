"""Hermetic end-to-end local-setting transaction contract."""
from __future__ import annotations

import base64
from contextlib import closing
import json
import sqlite3
from typing import Callable
from xml.etree import ElementTree

import pytest

from settings_constraints import CONTROL_WRITE_WHITELIST
from twin.state import CommandState, ControlIngress, IngressDisposition

from tests.v2.e2e.fakes import (
    FakeBoxEndpoint,
    LocalControlHarness,
    corrupt_crc,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.e2e,
    pytest.mark.enable_socket,
    pytest.mark.local_control,
]


HarnessFactory = Callable[..., LocalControlHarness]


def _xml_text(frame: bytes, tag: str) -> str | None:
    root = ElementTree.fromstring(frame.decode("utf-8").strip())
    node = root.find(tag)
    return node.text if node is not None else None


async def _complete_exact_event(
    harness: LocalControlHarness,
    command_id: str,
    *,
    event_id_set: int = 1_786_000_100,
) -> bytes:
    command = harness.store.read_command(command_id)
    event = harness.setting_event(command, event_id_set=event_id_set)
    await harness.fake_box.send(event)
    await harness.wait_until(
        lambda: harness.store.read_command(command_id).state
        is CommandState.CONFIRMED
        and bool(harness.confirmed_state_messages())
    )
    return event


async def test_e2e_online_cloud_priority_then_local_batch(
    harness: LocalControlHarness,
) -> None:
    """Cloud Setting owns the dialogue before queued local commands run."""
    first = await harness.enqueue("tbl_box_prms", "MODE", "2")
    second = await harness.enqueue("tbl_box_prms", "BAT_AC", "3")
    poll = harness.isnewset_poll()
    await harness.fake_box.send(poll)
    assert await harness.fake_cloud.read_frame() == poll

    cloud_setting = harness.cloud_setting("tbl_box_prms", "MODE", "1")
    await harness.fake_cloud.send(cloud_setting)
    assert await harness.fake_box.read_frame() == cloud_setting
    cloud_ack = harness.setting_ack()
    await harness.fake_box.send(cloud_ack)
    assert await harness.fake_cloud.read_frame() == cloud_ack

    deferred_end = harness.cloud_end(marker="cloud-first")
    await harness.fake_cloud.send(deferred_end)
    local_first = await harness.fake_box.read_frame()
    assert _xml_text(local_first, "TblItem") == "MODE"
    assert _xml_text(local_first, "NewValue") == "2"
    assert deferred_end != local_first

    await harness.fake_box.send(
        harness.setting_ack(rdt="2026-08-06 10:12:03")
    )
    local_second = await harness.fake_box.read_frame()
    assert _xml_text(local_second, "TblItem") == "BAT_AC"
    assert _xml_text(local_second, "NewValue") == "3"
    await harness.fake_box.send(
        harness.setting_ack(rdt="2026-08-06 10:12:04")
    )
    assert await harness.fake_box.read_frame() == deferred_end

    assert harness.store.read_command(first.command_id).state is CommandState.AWAITING_EVENT
    assert harness.store.read_command(second.command_id).state is CommandState.AWAITING_EVENT
    assert harness.confirmed_state_messages() == []


async def test_e2e_foreign_session_cannot_advance_active_command(
    harness: LocalControlHarness,
) -> None:
    """Only the socket session owning an attempt may ACK its local write."""
    command, setting, _end = await harness.begin_local_delivery()
    active = harness.store.read_command(command.command_id)
    original_attempt = harness.store.read_attempt(command.command_id, 1)

    server = harness.proxy._server  # pylint: disable=protected-access
    assert server is not None and server.sockets
    foreign_box = FakeBoxEndpoint()
    await foreign_box.connect(int(server.sockets[0].getsockname()[1]))
    foreign_cloud = await harness.fake_cloud.wait_connected()
    try:
        foreign_poll = harness.isnewset_poll(message_id=14_000_010, id_set=1_786_000_010)
        await foreign_box.send(foreign_poll)
        assert await foreign_cloud.read_frame() == foreign_poll
        await foreign_cloud.send(harness.cloud_end(marker="foreign"))
        assert await foreign_box.read_frame() == harness.cloud_end(marker="foreign")

        await foreign_box.send(harness.setting_ack())
        assert await foreign_cloud.read_frame() == harness.setting_ack()
        unchanged = harness.store.read_command(command.command_id)
        assert unchanged.state is CommandState.AWAITING_ACK
        assert unchanged.active_session_id == active.active_session_id
        assert harness.store.read_attempt(command.command_id, 1) == original_attempt
        assert setting == unchanged.last_wire_frame
    finally:
        await foreign_box.close()


async def test_e2e_ack_is_delivery_only_until_exact_event(
    harness: LocalControlHarness,
) -> None:
    """A local ACK records delivery but cannot assert device execution."""
    command = await harness.deliver_and_ack()
    assert command.state is CommandState.AWAITING_EVENT
    assert harness.confirmed_state_messages() == []

    exact_event = await _complete_exact_event(harness, command.command_id)
    assert harness.store.read_command(command.command_id).state is CommandState.CONFIRMED
    publications = harness.confirmed_state_messages()
    assert len(publications) == 1
    assert json.loads(str(publications[0].payload))["MODE"] == 2
    assert exact_event not in await _captured_cloud_frames(harness)


async def test_e2e_matching_event_confirms_and_nonmatching_event_does_not(
    harness: LocalControlHarness,
) -> None:
    """Device, table, item and canonical value must all match event evidence."""
    command = await harness.deliver_and_ack()
    mismatches = (
        harness.setting_event(command, device_id="999", event_id_set=1_786_000_101),
        harness.setting_event(command, table="tbl_batt_prms", event_id_set=1_786_000_102),
        harness.setting_event(command, key="BAT_AC", event_id_set=1_786_000_103),
        harness.setting_event(command, value="3", event_id_set=1_786_000_104),
    )
    for event in mismatches:
        await harness.fake_box.send(event)
        assert await harness.fake_cloud.read_frame() == event
        assert harness.store.read_command(command.command_id).state is CommandState.AWAITING_EVENT
        assert harness.confirmed_state_messages() == []

    await _complete_exact_event(harness, command.command_id, event_id_set=1_786_000_105)
    assert len(harness.confirmed_state_messages()) == 1


async def _captured_cloud_frames(harness: LocalControlHarness) -> tuple[bytes, ...]:
    """Read captured BOX-to-cloud frames without touching the live stream."""
    with closing(sqlite3.connect(harness.capture_path)) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(frames)")]
        if "direction" not in columns or "raw_b64" not in columns:
            return ()
        rows = connection.execute(
            "SELECT raw_b64 FROM frames WHERE direction = 'box_to_cloud' ORDER BY id"
        ).fetchall()
    return tuple(base64.b64decode(str(row[0])) for row in rows if row[0] is not None)


async def test_e2e_rapid_same_key_updates_do_not_overwrite_attempted_command(
    harness: LocalControlHarness,
) -> None:
    """An attempted value is immutable while only unsent successors coalesce."""
    first, first_wire, deferred_end = await harness.begin_local_delivery(value="2")
    replaced = await harness.enqueue("tbl_box_prms", "MODE", "3")
    successor = await harness.enqueue("tbl_box_prms", "MODE", "4")

    assert harness.store.read_command(first.command_id).last_wire_frame == first_wire
    assert harness.store.read_command(first.command_id).value_text == "2"
    assert harness.store.read_command(replaced.command_id).state is CommandState.SUPERSEDED
    assert harness.store.read_command(successor.command_id).state is CommandState.PENDING

    await harness.fake_box.send(
        harness.setting_ack(rdt="2026-08-06 10:12:03")
    )
    successor_wire = await harness.fake_box.read_frame()
    assert _xml_text(successor_wire, "NewValue") == "4"
    assert first_wire != successor_wire
    await harness.fake_box.send(
        harness.setting_ack(rdt="2026-08-06 10:12:04")
    )
    assert await harness.fake_box.read_frame() == deferred_end

    first_event = await _complete_exact_event(harness, first.command_id)
    assert await harness.fake_cloud.read_frame() == first_event
    successor_event = await _complete_exact_event(
        harness, successor.command_id, event_id_set=1_786_000_101
    )
    assert await harness.fake_cloud.read_frame() == successor_event
    assert [
        json.loads(str(message.payload))["MODE"]
        for message in harness.confirmed_state_messages()
    ] == [2, 4]


async def test_e2e_restart_retries_with_stable_identity(
    harness: LocalControlHarness,
) -> None:
    """A disconnect retries the same command/audit/wire identity after restart."""
    command, first_wire, _end = await harness.begin_local_delivery()
    attempted = harness.store.read_command(command.command_id)
    first_attempt = harness.store.read_attempt(command.command_id, 1)
    await harness.fake_box.close()
    await harness.wait_until(
        lambda: harness.store.read_command(command.command_id).state
        is CommandState.RETRY_PENDING
    )

    harness.advance_clock(2_000)
    await harness.restart_proxy_and_store()
    poll = harness.isnewset_poll(message_id=14_000_010, id_set=1_786_000_010)
    await harness.fake_box.send(poll)
    assert await harness.fake_cloud.read_frame() == poll
    deferred_end = harness.cloud_end(marker="retry")
    await harness.fake_cloud.send(deferred_end)
    second_wire = await harness.fake_box.read_frame()
    second_attempt = harness.store.read_attempt(command.command_id, 2)
    retried = harness.store.read_command(command.command_id)

    assert retried.command_id == command.command_id
    assert retried.audit_id == command.audit_id
    assert retried.attempt_count == 2
    assert (retried.wire_id, retried.wire_id_set, retried.wire_dt) == (
        attempted.wire_id,
        attempted.wire_id_set,
        attempted.wire_dt,
    )
    assert (_xml_text(first_wire, "ID"), _xml_text(first_wire, "ID_Set")) == (
        _xml_text(second_wire, "ID"),
        _xml_text(second_wire, "ID_Set"),
    )
    assert first_attempt.ver_text != second_attempt.ver_text
    assert first_wire != second_wire

    await harness.fake_box.send(
        harness.setting_ack(rdt="2026-08-06 10:12:05")
    )
    assert await harness.fake_box.read_frame() == deferred_end


async def test_e2e_retry_limit_and_terminal_nack(
    harness_factory: HarnessFactory,
) -> None:
    """The configured retry ceiling and every exact NACK are terminal."""
    limited = harness_factory(max_attempts=1)
    await limited.start()
    try:
        command, _setting, _end = await limited.begin_local_delivery()
        await limited.fake_box.close()
        await limited.wait_until(
            lambda: limited.store.read_command(command.command_id).state
            is CommandState.FAILED
        )
        limited.advance_clock(2_000)
        await limited.restart_proxy_and_store()
        poll = limited.isnewset_poll(message_id=14_000_020, id_set=1_786_000_020)
        await limited.fake_box.send(poll)
        assert await limited.fake_cloud.read_frame() == poll
        terminal_end = limited.cloud_end(marker="attempt-limit")
        await limited.fake_cloud.send(terminal_end)
        assert await limited.fake_box.read_frame() == terminal_end
        assert limited.store.read_command(command.command_id).attempt_count == 1
    finally:
        await limited.stop()

    nacked = harness_factory()
    await nacked.start()
    try:
        command, _setting, deferred_end = await nacked.begin_local_delivery()
        nack = nacked.setting_ack(result="NACK", reason="WC")
        await nacked.fake_box.send(nack)
        assert await nacked.fake_box.read_frame() == deferred_end
        assert nacked.store.read_command(command.command_id).state is CommandState.FAILED
        assert nacked.confirmed_state_messages() == []
    finally:
        await nacked.stop()


async def test_e2e_invalid_crc_never_selects_advances_or_confirms(
    harness: LocalControlHarness,
) -> None:
    """CRC-invalid bytes remain evidence only and never mutate a transaction."""
    command = await harness.enqueue("tbl_box_prms", "MODE", "2")
    bad_poll = corrupt_crc(harness.isnewset_poll())
    await harness.fake_box.send(bad_poll)
    assert await harness.fake_cloud.read_frame() == bad_poll
    assert harness.store.read_command(command.command_id).state is CommandState.PENDING

    poll = harness.isnewset_poll(message_id=14_000_030, id_set=1_786_000_030)
    await harness.fake_box.send(poll)
    assert await harness.fake_cloud.read_frame() == poll
    await harness.fake_cloud.send(harness.cloud_end(marker="bad-ack"))
    await harness.fake_box.read_frame()
    await harness.fake_box.send(corrupt_crc(harness.setting_ack()))
    await harness.wait_until(
        lambda: harness.store.read_command(command.command_id).state
        in {CommandState.RETRY_PENDING, CommandState.FAILED}
    )
    assert harness.confirmed_state_messages() == []
    assert all(
        transition.reason != "ack_received"
        for transition in harness.store.read_transitions(command.command_id)
    )


async def test_e2e_partial_and_coalesced_frames_preserve_bytes_and_order(
    harness: LocalControlHarness,
) -> None:
    """Assembler boundaries survive partial reads and coalesced TCP writes."""
    command = await harness.enqueue("tbl_box_prms", "MODE", "2")
    sensor = harness.sensor_frame("before-poll")
    poll = harness.isnewset_poll(message_id=14_000_040, id_set=1_786_000_040)
    joined = sensor + poll
    await harness.fake_box.send_chunks(
        (joined[:7], joined[7:len(sensor) + 5], joined[len(sensor) + 5:])
    )
    assert await harness.fake_cloud.read_frame() == sensor
    assert await harness.fake_cloud.read_frame() == poll

    sensor_response = harness.frame(result="ACK", reason="Sensor")
    await harness.fake_cloud.send(sensor_response)
    assert await harness.fake_box.read_frame() == sensor_response
    deferred_end = harness.cloud_end(marker="framing")
    await harness.fake_cloud.send(deferred_end)
    setting = await harness.fake_box.read_frame()
    assert setting == harness.store.read_command(command.command_id).last_wire_frame
    await harness.fake_box.send(harness.setting_ack())
    assert await harness.fake_box.read_frame() == deferred_end


async def test_e2e_non_setting_polls_never_trigger_delivery(
    harness: LocalControlHarness,
) -> None:
    """Firmware, weather and table requests cannot open a Setting batch."""
    command = await harness.enqueue("tbl_box_prms", "MODE", "2")
    requests = (
        harness.frame(result="IsNewFW", device_id="123"),
        harness.frame(result="IsNewWeather", device_id="123"),
        harness.sensor_frame("table-upload"),
    )
    for index, request_frame in enumerate(requests):
        await harness.fake_box.send(request_frame)
        assert await harness.fake_cloud.read_frame() == request_frame
        response = harness.cloud_end(marker=f"non-setting-{index}")
        await harness.fake_cloud.send(response)
        assert await harness.fake_box.read_frame() == response
        assert harness.store.read_command(command.command_id).state is CommandState.PENDING
    assert harness.confirmed_state_messages() == []


async def test_e2e_disabled_control_has_no_subscription_discovery_or_write(
    harness_factory: HarnessFactory,
) -> None:
    """Disabled control is fail-closed while passive execution evidence survives."""
    enabled = harness_factory()
    await enabled.start()
    command = await enabled.deliver_and_ack()
    root = enabled.root
    validator = enabled.validate_config
    await enabled.stop()

    disabled = LocalControlHarness(
        root=root,
        validate_config=validator,
        control_enabled=False,
    )
    await disabled.start()
    try:
        assert disabled.handler is None
        assert disabled.fake_mqtt.registered_subscriptions == frozenset()
        tombstones = [
            item for item in disabled.fake_mqtt.published if item.topic.endswith("/config")
        ]
        assert len(tombstones) == 3 * sum(
            len(keys) for keys in CONTROL_WRITE_WHITELIST.values()
        )
        assert all(item.payload == b"" and item.retain for item in tombstones)

        event = disabled.setting_event(command)
        await disabled.fake_box.send(event)
        assert await disabled.fake_cloud.read_frame() == event
        await disabled.wait_until(
            lambda: disabled.store.read_command(command.command_id).state
            is CommandState.CONFIRMED
            and bool(disabled.confirmed_state_messages())
        )

        disabled.store.enqueue_command(
            ControlIngress(
                "disabled-seed",
                disabled.now_ms(),
                "test/disabled",
                "123",
                False,
                '{"value":"3"}',
            ),
            device_id="123",
            table_name="tbl_box_prms",
            item_name="BAT_AC",
            value_text="3",
        )
        poll = disabled.isnewset_poll(message_id=14_000_050, id_set=1_786_000_050)
        await disabled.fake_box.send(poll)
        assert await disabled.fake_cloud.read_frame() == poll
        end = disabled.cloud_end(marker="disabled")
        await disabled.fake_cloud.send(end)
        assert await disabled.fake_box.read_frame() == end
        assert disabled.command_states()[-1] == ("3", CommandState.PENDING)
    finally:
        await disabled.stop()


async def test_e2e_retained_control_never_enters_local_batch(
    harness: LocalControlHarness,
) -> None:
    """Retained primary and compatibility ingress are rejected before parsing."""
    primary = "oig/123/control/set"
    compatibility = "oig_local/123/set/tbl_box_prms/MODE"
    harness.fake_mqtt.emit(primary, b'{"table":"tbl_box_prms"}', retain=True)
    harness.fake_mqtt.emit(compatibility, b"2", retain=True)
    await harness.wait_until(lambda: _ingress_count(harness) == 2)

    with closing(sqlite3.connect(harness.db_path)) as connection:
        rows = connection.execute(
            "SELECT retain, disposition, command_id FROM control_ingress_audit "
            "ORDER BY received_at_ms, ingress_id"
        ).fetchall()
    assert rows == [
        (1, IngressDisposition.REJECTED_RETAINED.value, None),
        (1, IngressDisposition.REJECTED_RETAINED.value, None),
    ]
    assert harness.command_ids() == []

    accepted = await harness.enqueue("tbl_box_prms", "MODE", "2")
    assert harness.store.read_command(accepted.command_id).state is CommandState.PENDING


def _ingress_count(harness: LocalControlHarness) -> int:
    with closing(sqlite3.connect(harness.db_path)) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM control_ingress_audit").fetchone()[0])


async def test_e2e_no_delivery_before_valid_device_identity(
    harness_factory: HarnessFactory,
) -> None:
    """Subscriptions and local writes start only after full validated identity."""
    runtime = harness_factory(known_identity=False)
    await runtime.start()
    try:
        assert runtime.bound_device_id is None
        assert runtime.handler is None
        assert runtime.fake_mqtt.registered_subscriptions == frozenset()

        incomplete = runtime.isnewset_poll(device_id="")
        await runtime.fake_box.send(incomplete)
        assert await runtime.fake_cloud.read_frame() == incomplete
        first_end = runtime.cloud_end(marker="identity-missing")
        await runtime.fake_cloud.send(first_end)
        assert await runtime.fake_box.read_frame() == first_end
        assert runtime.handler is None

        valid = runtime.isnewset_poll(message_id=14_000_060, id_set=1_786_000_060)
        await runtime.fake_box.send(valid)
        assert await runtime.fake_cloud.read_frame() == valid
        await runtime.wait_until(lambda: runtime.handler is not None)
        assert runtime.bound_device_id == "123"
        assert runtime.fake_mqtt.registered_subscriptions == frozenset(
            {"oig/123/control/set", "oig_local/123/set/#"}
        )
        second_end = runtime.cloud_end(marker="identity-valid")
        await runtime.fake_cloud.send(second_end)
        assert await runtime.fake_box.read_frame() == second_end

        command, setting, _end = await runtime.begin_local_delivery()
        assert setting == runtime.store.read_command(command.command_id).last_wire_frame
    finally:
        await runtime.stop()


async def test_e2e_audit_identity_survives_all_write_outcomes(
    harness: LocalControlHarness,
) -> None:
    """Ingress, attempts, capture, transitions and telemetry share one identity."""
    command, setting, deferred_end = await harness.begin_local_delivery()
    await harness.fake_box.send(harness.setting_ack())
    assert await harness.fake_box.read_frame() == deferred_end
    event = await _complete_exact_event(harness, command.command_id)
    assert await harness.fake_cloud.read_frame() == event
    await harness.wait_until(
        lambda: len(harness.audit_records)
        >= len(harness.store.read_transitions(command.command_id))
    )

    persisted = harness.store.read_command(command.command_id)
    transitions = harness.store.read_transitions(command.command_id)
    assert persisted.audit_id == command.audit_id
    assert {item.command_id for item in transitions} == {command.command_id}
    assert {item.audit_id for item in transitions} == {command.audit_id}
    assert {item.reason for item in transitions} >= {
        "accepted_ingress",
        "selected",
        "attempt_prepared",
        "write_started",
        "attempt_drained",
        "ack_received",
        "event_confirmed",
    }

    records = [item for item in harness.audit_records if item.command_id == command.command_id]
    assert {item.audit_id for item in records} == {command.audit_id}
    assert {item.transition_id for item in records} == {
        item.transition_id for item in transitions
    }
    attempt = harness.store.read_attempt(command.command_id, 1)
    assert attempt.wire_frame == setting
    await harness.wait_until(lambda: bool(_capture_attempt_rows(harness, command.command_id)))
    assert _capture_attempt_rows(harness, command.command_id) == [
        (command.command_id, command.audit_id, 1)
    ]


def _capture_attempt_rows(
    harness: LocalControlHarness, command_id: str
) -> list[tuple[str, str, int]]:
    with closing(sqlite3.connect(harness.capture_path)) as connection:
        return [
            (str(row[0]), str(row[1]), int(row[2]))
            for row in connection.execute(
                "SELECT command_id, audit_id, attempt_number FROM frames "
                "WHERE command_id = ? ORDER BY id",
                (command_id,),
            )
        ]
