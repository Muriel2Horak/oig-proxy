"""Defensive branch contracts for connection-local Setting state."""
from __future__ import annotations

from dataclasses import replace

import pytest

from protocol.frame import AssembledFrame
from proxy.dialog import (
    BoundedFrameQueue,
    CyclePhase,
    DialogStateError,
    RequestKind,
    SessionRoute,
    SettingDialog,
)
from twin.state import ActiveLocalAttempt


def _cycle(kind: RequestKind = RequestKind.IS_NEW_SET) -> SettingDialog:
    dialog = SettingDialog("session-1", SessionRoute.ONLINE)
    dialog.bind_device("device-1")
    dialog.open_forwarded_request(
        kind=kind,
        request_raw=b"request",
        opened_at_monotonic=1.0,
        cloud_timeout_s=2.0,
    )
    return dialog


@pytest.mark.parametrize("value", (True, "1"))
def test_bounded_queue_rejects_non_integer_limits(value: object) -> None:
    with pytest.raises(TypeError, match="integer"):
        BoundedFrameQueue(max_bytes=value)  # type: ignore[arg-type]


def test_bounded_queue_rejects_negative_limit_and_non_frame() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        BoundedFrameQueue(max_bytes=-1)
    queue = BoundedFrameQueue()
    with pytest.raises(TypeError, match="AssembledFrame"):
        queue.append(b"frame")  # type: ignore[arg-type]


@pytest.mark.parametrize("session_id", ("", 1))
def test_dialog_rejects_invalid_session(session_id: object) -> None:
    with pytest.raises(ValueError, match="session_id"):
        SettingDialog(session_id, SessionRoute.ONLINE)  # type: ignore[arg-type]


def test_dialog_rejects_invalid_route_and_device_inputs() -> None:
    with pytest.raises(TypeError, match="route"):
        SettingDialog("session", "online")  # type: ignore[arg-type]
    dialog = SettingDialog("session", SessionRoute.ONLINE)
    for device_id in ("", 1):
        with pytest.raises(ValueError, match="device_id"):
            dialog.bind_device(device_id)  # type: ignore[arg-type]


def test_open_request_rejects_invalid_kind_and_raw_bytes() -> None:
    dialog = SettingDialog("session", SessionRoute.ONLINE)
    with pytest.raises(TypeError, match="RequestKind"):
        dialog.open_forwarded_request(
            kind="is_new_set",  # type: ignore[arg-type]
            request_raw=b"raw",
            opened_at_monotonic=1.0,
            cloud_timeout_s=None,
        )
    with pytest.raises(TypeError, match="request_raw"):
        dialog.open_forwarded_request(
            kind=RequestKind.IS_NEW_SET,
            request_raw=bytearray(b"raw"),  # type: ignore[arg-type]
            opened_at_monotonic=1.0,
            cloud_timeout_s=None,
        )


def test_close_cycle_rejects_active_attempt_or_deferred_end(
    active_local_attempt: ActiveLocalAttempt,
) -> None:
    active = _cycle()
    active.deferred_end = b"end"
    active.begin_local_attempt(active_local_attempt)
    with pytest.raises(DialogStateError, match="active"):
        active.close_current_expectation()

    deferred = _cycle()
    deferred.deferred_end = b"end"
    with pytest.raises(DialogStateError, match="deferred END"):
        deferred.close_current_expectation()


def test_cloud_setting_rejects_wrong_kind_taint_phase_and_raw_type() -> None:
    wrong_kind = _cycle(RequestKind.SINGLE_RESPONSE)
    with pytest.raises(DialogStateError, match="IsNewSet"):
        wrong_kind.mark_cloud_setting(b"setting")

    tainted = _cycle()
    tainted.taint_current_cycle()
    with pytest.raises(DialogStateError, match="tainted"):
        tainted.mark_cloud_setting(b"setting")

    wrong_phase = _cycle()
    wrong_phase.current_expectation().phase = CyclePhase.LOCAL_AWAITING_ACK  # type: ignore[union-attr]
    with pytest.raises(DialogStateError, match="out of sequence"):
        wrong_phase.mark_cloud_setting(b"setting")

    with pytest.raises(TypeError, match="raw"):
        _cycle().mark_cloud_setting("setting")  # type: ignore[arg-type]


def test_cloud_ack_rejects_taint_and_wrong_phase() -> None:
    tainted = _cycle()
    tainted.taint_current_cycle()
    with pytest.raises(DialogStateError, match="tainted"):
        tainted.mark_cloud_setting_ack_forwarded(b"ack")

    wrong_phase = _cycle()
    with pytest.raises(DialogStateError, match="out of sequence"):
        wrong_phase.mark_cloud_setting_ack_forwarded(b"ack")


def test_terminal_end_rejects_wrong_phase_and_duplicate() -> None:
    wrong_phase = _cycle()
    wrong_phase.mark_cloud_setting(b"setting")
    with pytest.raises(DialogStateError, match="out of sequence"):
        wrong_phase.defer_correlated_terminal_end(b"end")

    duplicate = _cycle()
    duplicate.defer_correlated_terminal_end(b"end")
    with pytest.raises(DialogStateError, match="already deferred"):
        duplicate.defer_correlated_terminal_end(b"end-2")


def test_begin_and_replace_local_attempt_reject_missing_or_wrong_cycle(
    active_local_attempt: ActiveLocalAttempt,
) -> None:
    missing_end = _cycle()
    with pytest.raises(DialogStateError, match="deferred END"):
        missing_end.begin_local_attempt(active_local_attempt)

    wrong_kind = _cycle(RequestKind.SINGLE_RESPONSE)
    wrong_kind.deferred_end = b"end"
    with pytest.raises(DialogStateError, match="IsNewSet"):
        wrong_kind.begin_local_attempt(active_local_attempt)

    no_active = _cycle()
    no_active.deferred_end = b"end"
    with pytest.raises(DialogStateError, match="no local attempt"):
        no_active.replace_local_attempt(active_local_attempt)

    missing_replacement_end = _cycle()
    missing_replacement_end.deferred_end = b"end"
    missing_replacement_end.begin_local_attempt(active_local_attempt)
    missing_replacement_end.deferred_end = None
    with pytest.raises(DialogStateError, match="deferred END"):
        missing_replacement_end.replace_local_attempt(active_local_attempt)


def test_offline_attempt_methods_reject_wrong_route_and_state(
    active_local_attempt: ActiveLocalAttempt,
) -> None:
    online = SettingDialog("session-1", SessionRoute.ONLINE)
    with pytest.raises(DialogStateError, match="offline route"):
        online.begin_offline_attempt(active_local_attempt)
    with pytest.raises(DialogStateError, match="offline route"):
        online.replace_offline_attempt(active_local_attempt)
    with pytest.raises(DialogStateError, match="offline route"):
        online.close_offline_attempt()

    active = SettingDialog("session-1", SessionRoute.OFFLINE)
    active.begin_offline_attempt(active_local_attempt)
    with pytest.raises(DialogStateError, match="already active"):
        active.begin_offline_attempt(active_local_attempt)

    cloud_state = SettingDialog("session-1", SessionRoute.OFFLINE)
    cloud_state.open_forwarded_request(
        kind=RequestKind.IS_NEW_SET,
        request_raw=b"poll",
        opened_at_monotonic=1.0,
        cloud_timeout_s=None,
    )
    with pytest.raises(DialogStateError, match="cloud state"):
        cloud_state.begin_offline_attempt(active_local_attempt)

    empty = SettingDialog("session-1", SessionRoute.OFFLINE)
    with pytest.raises(DialogStateError, match="no local attempt"):
        empty.replace_offline_attempt(active_local_attempt)
    with pytest.raises(DialogStateError, match="no local attempt"):
        empty.close_offline_attempt()


def test_deferred_and_validation_helpers_reject_absent_or_foreign_attempts(
    active_local_attempt: ActiveLocalAttempt,
) -> None:
    dialog = SettingDialog("session-1", SessionRoute.ONLINE)
    with pytest.raises(DialogStateError, match="no terminal END"):
        dialog.take_deferred_end_and_close_cycle()
    with pytest.raises(DialogStateError, match="no forwarded request"):
        dialog.taint_current_cycle()

    cycle = _cycle()
    cycle.deferred_end = b"end"
    foreign_device = replace(active_local_attempt, device_id="other")
    with pytest.raises(DialogStateError, match="different device"):
        cycle.begin_local_attempt(foreign_device)


def test_offline_attempt_rejects_non_attempt_after_route_check() -> None:
    dialog = SettingDialog("session-1", SessionRoute.OFFLINE)
    with pytest.raises(TypeError, match="ActiveLocalAttempt"):
        dialog.begin_offline_attempt(AssembledFrame(b"raw", 1))  # type: ignore[arg-type]
