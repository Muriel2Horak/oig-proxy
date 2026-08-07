"""Connection-local Setting dialogue contract tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from protocol.frame import AssembledFrame
from proxy.dialog import (
    BoundedFrameQueue,
    CyclePhase,
    DialogStateError,
    HeldFrameOverflow,
    RequestKind,
    SessionRoute,
    SettingDialog,
)
from twin.state import ActiveLocalAttempt, AttemptWriteOutcome, DeliveryTrigger


def _active_attempt(
    command_id: str = "command-1",
    attempt_number: int = 1,
    *,
    session_id: str = "session-1",
    device_id: str = "device-1",
) -> ActiveLocalAttempt:
    return ActiveLocalAttempt(
        command_id=command_id,
        audit_id=f"audit-{command_id}",
        device_id=device_id,
        attempt_number=attempt_number,
        session_id=session_id,
        ack_deadline_ms=30_000,
        wire_frame=f"wire-{command_id}-{attempt_number}".encode("ascii"),
        write_outcome=AttemptWriteOutcome.PREPARED,
    )


def _open_isnewset(dialog: SettingDialog, *, opened_at: float = 1.0) -> None:
    dialog.open_forwarded_request(
        kind=RequestKind.IS_NEW_SET,
        request_raw=b"poll\r\n",
        opened_at_monotonic=opened_at,
        cloud_timeout_s=30.0,
    )


def _cloud_waiting_dialog() -> SettingDialog:
    dialog = SettingDialog(session_id="session-1", route=SessionRoute.ONLINE)
    _open_isnewset(dialog)
    return dialog


def _cloud_owned_dialog() -> SettingDialog:
    dialog = _cloud_waiting_dialog()
    dialog.mark_cloud_setting(b"cloud-setting\r\n")
    return dialog


def test_cloud_setting_marks_cycle_cloud_owned_without_local_claim() -> None:
    dialog = _cloud_waiting_dialog()
    expectation = dialog.current_expectation()

    dialog.mark_cloud_setting(b"cloud-setting\r\n")

    assert dialog.current_expectation() is expectation
    assert expectation is not None
    assert expectation.phase is CyclePhase.WAITING_BOX_CLOUD_ACK
    assert expectation.cloud_setting_count == 1
    assert dialog.active_attempt is None


def test_cloud_setting_ack_is_continuation_not_new_request() -> None:
    dialog = _cloud_owned_dialog()
    size = dialog.expectation_count

    dialog.mark_cloud_setting_ack_forwarded(b"box-ack\r\n")

    assert dialog.expectation_count == size
    assert dialog.current_expectation() is not None
    assert dialog.current_expectation().phase is CyclePhase.WAITING_CLOUD


def test_deferred_end_is_returned_byte_exact_after_final_ack() -> None:
    raw_end = b"<Frame><Result>END</Result><CRC>12345</CRC></Frame>\r\n"
    dialog = _cloud_waiting_dialog()

    trigger = dialog.defer_correlated_terminal_end(raw_end)
    dialog.begin_local_attempt(_active_attempt())

    assert trigger is DeliveryTrigger.CORRELATED_CLOUD_END
    assert dialog.take_deferred_end_and_close_cycle() == raw_end
    assert dialog.current_expectation() is None
    assert dialog.active_attempt is None


def test_local_delivery_trigger_accepts_only_isnewset_fifo_head() -> None:
    dialog = SettingDialog(session_id="session-1", route=SessionRoute.ONLINE)
    dialog.open_forwarded_request(
        kind=RequestKind.SINGLE_RESPONSE,
        request_raw=b"weather",
        opened_at_monotonic=1.0,
        cloud_timeout_s=None,
    )
    _open_isnewset(dialog)

    with pytest.raises(DialogStateError, match="not an IsNewSet cycle"):
        dialog.defer_correlated_terminal_end(b"end")

    assert dialog.expectation_count == 2


def test_tainted_cycle_cannot_trigger_local_delivery() -> None:
    dialog = _cloud_waiting_dialog()
    dialog.taint_current_cycle()

    with pytest.raises(DialogStateError, match="tainted"):
        dialog.defer_correlated_terminal_end(b"end")

    assert dialog.current_expectation() is not None
    assert dialog.current_expectation().phase is CyclePhase.TAINTED


def test_cycle_tainted_after_deferred_end_cannot_begin_local_attempt() -> None:
    dialog = _cloud_waiting_dialog()
    dialog.defer_correlated_terminal_end(b"end")
    dialog.taint_current_cycle()

    with pytest.raises(DialogStateError, match="tainted"):
        dialog.begin_local_attempt(_active_attempt())

    assert dialog.active_attempt is None
    assert dialog.current_expectation() is not None
    assert dialog.current_expectation().phase is CyclePhase.TAINTED


def test_tainted_active_cycle_cannot_replace_local_attempt() -> None:
    dialog = _cloud_waiting_dialog()
    dialog.defer_correlated_terminal_end(b"end")
    dialog.begin_local_attempt(_active_attempt())
    dialog.taint_current_cycle()

    with pytest.raises(DialogStateError, match="tainted"):
        dialog.replace_local_attempt(_active_attempt("command-2", 2))

    assert dialog.active_attempt is not None
    assert dialog.active_attempt.command_id == "command-1"


def test_later_box_and_cloud_frames_are_held_in_separate_fifo_queues() -> None:
    dialog = _cloud_waiting_dialog()
    box_frames = (
        AssembledFrame(b"box-1", 1),
        AssembledFrame(b"box-2", 2),
    )
    cloud_frames = (
        AssembledFrame(b"cloud-1", 3),
        AssembledFrame(b"cloud-2", 4),
    )

    for frame in box_frames:
        dialog.hold_box_frame(frame)
    for frame in cloud_frames:
        dialog.hold_cloud_frame(frame)

    assert dialog.drain_held_box() == box_frames
    assert dialog.drain_held_cloud() == cloud_frames
    assert dialog.drain_held_box() == ()
    assert dialog.drain_held_cloud() == ()


def test_held_frame_queue_allows_exact_one_mib() -> None:
    held = BoundedFrameQueue()
    frame = AssembledFrame(b"x" * 1_048_576, 1)

    held.append(frame)

    assert held.byte_count == 1_048_576
    assert held.drain() == (frame,)
    assert held.byte_count == 0


def test_held_frame_queue_rejects_one_byte_over_limit_without_mutation() -> None:
    held = BoundedFrameQueue()
    first = AssembledFrame(b"x" * 1_048_576, 1)
    held.append(first)

    with pytest.raises(HeldFrameOverflow, match="1048576"):
        held.append(AssembledFrame(b"y", 2))

    assert held.byte_count == 1_048_576
    assert held.drain() == (first,)


def test_connection_rejects_device_identity_change() -> None:
    dialog = SettingDialog(session_id="session-1", route=SessionRoute.ONLINE)

    assert dialog.bind_device("device-1") is True
    assert dialog.bind_device("device-1") is False
    with pytest.raises(DialogStateError, match="device identity"):
        dialog.bind_device("device-2")

    assert dialog.bound_device_id == "device-1"


def test_route_cannot_change_after_dialog_creation() -> None:
    dialog = SettingDialog(session_id="session-1", route=SessionRoute.ONLINE)

    with pytest.raises(FrozenInstanceError):
        dialog.route = SessionRoute.OFFLINE

    assert dialog.route is SessionRoute.ONLINE


def test_cloud_deadline_remains_absolute_across_multiple_settings() -> None:
    dialog = _cloud_waiting_dialog()
    expectation = dialog.current_expectation()
    assert expectation is not None
    assert expectation.deadline_monotonic == 31.0

    dialog.mark_cloud_setting(b"setting-1")
    dialog.mark_cloud_setting_ack_forwarded(b"ack-1")
    dialog.mark_cloud_setting(b"setting-2")
    dialog.mark_cloud_setting_ack_forwarded(b"ack-2")

    assert dialog.current_expectation() is expectation
    assert expectation.deadline_monotonic == 31.0
    assert dialog.is_cloud_deadline_expired(30.999) is False
    assert dialog.is_cloud_deadline_expired(31.0) is True


def test_local_attempt_requires_dialog_session_and_single_active_attempt() -> None:
    dialog = _cloud_waiting_dialog()
    dialog.bind_device("device-1")
    dialog.defer_correlated_terminal_end(b"end")

    with pytest.raises(DialogStateError, match="session"):
        dialog.begin_local_attempt(_active_attempt(session_id="other-session"))

    first = _active_attempt()
    dialog.begin_local_attempt(first)
    with pytest.raises(DialogStateError, match="already active"):
        dialog.begin_local_attempt(_active_attempt("command-2"))

    replacement = _active_attempt("command-2", 2)
    dialog.replace_local_attempt(replacement)
    assert dialog.active_attempt is replacement


def test_clear_socket_state_erases_all_connection_local_mutable_state() -> None:
    dialog = _cloud_waiting_dialog()
    dialog.bind_device("device-1")
    dialog.defer_correlated_terminal_end(b"deferred-end")
    dialog.begin_local_attempt(_active_attempt())
    dialog.hold_box_frame(AssembledFrame(b"box", 1))
    dialog.hold_cloud_frame(AssembledFrame(b"cloud", 2))

    dialog.clear_socket_state()

    assert dialog.current_expectation() is None
    assert dialog.expectation_count == 0
    assert dialog.deferred_end is None
    assert dialog.active_attempt is None
    assert dialog.bound_device_id is None
    assert dialog.drain_held_box() == ()
    assert dialog.drain_held_cloud() == ()
    assert dialog.session_id == "session-1"
    assert dialog.route is SessionRoute.ONLINE
