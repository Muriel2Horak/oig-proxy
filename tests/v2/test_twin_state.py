"""Exact immutable contracts for durable twin transaction state."""

# pyright: reportMissingImports=false
# pylint: disable=import-error,missing-function-docstring

from dataclasses import FrozenInstanceError, fields
from typing import Any

import pytest

from twin.state import (
    AckResult,
    AttemptRenderContext,
    AttemptWriteOutcome,
    AuditDeliveryDecision,
    AuditDeliveryState,
    ClaimDisposition,
    ClaimResult,
    CommandAttempt,
    CommandState,
    CommandTransition,
    ControlIngress,
    ControlPolicy,
    ConfirmedSetting,
    DeviceState,
    EnqueueResult,
    EventDisposition,
    EventMatchResult,
    EventTimeoutCandidate,
    IngressDisposition,
    LocalResponseDisposition,
    NackResult,
    PragmaSnapshot,
    RecoveryReport,
    RenderedAttempt,
    RetryReason,
    SettingEventReceipt,
    StoreStatus,
    SweepReport,
    TERMINAL_STATES,
    TwinCommand,
    TransitionAuditSnapshot,
)


def test_audit_delivery_decision_rejects_accounting_integrity_drift() -> None:
    decision = AuditDeliveryDecision(
        transition_id=1,
        audit_id="audit-00000000000000000000000000000000",
        command_id="cmd-00000000000000000000000000000000",
        canonical_payload_sha256=bytes(range(32)),
        decision_integrity_sha256=bytes.fromhex(
            "c63ff2ac7a177abb8738fcabbfe55950d"
            "f3d4800b48546784ac77654dd95572a"
        ),
        raw_bytes=7,
        payload_capped=False,
        state=AuditDeliveryState.PENDING,
    )

    with pytest.raises(ValueError, match="decision integrity"):
        decision.__class__(
            transition_id=decision.transition_id,
            audit_id=decision.audit_id,
            command_id=decision.command_id,
            canonical_payload_sha256=decision.canonical_payload_sha256,
            decision_integrity_sha256=decision.decision_integrity_sha256,
            raw_bytes=8,
            payload_capped=decision.payload_capped,
            state=decision.state,
        )


def test_command_state_values_and_terminal_set_are_exact() -> None:
    assert [state.value for state in CommandState] == [
        "pending",
        "retry_pending",
        "awaiting_ack",
        "awaiting_event",
        "confirmed",
        "incomplete",
        "failed",
        "expired",
        "superseded",
    ]
    assert TERMINAL_STATES == frozenset(
        {
            CommandState.CONFIRMED,
            CommandState.INCOMPLETE,
            CommandState.FAILED,
            CommandState.EXPIRED,
            CommandState.SUPERSEDED,
        }
    )


def test_write_outcome_values_are_exact() -> None:
    assert [outcome.value for outcome in AttemptWriteOutcome] == [
        "prepared",
        "started",
        "drained",
        "unknown",
        "failed",
    ]


def test_claim_disposition_values_are_exact() -> None:
    assert [disposition.value for disposition in ClaimDisposition] == [
        "prepared",
        "no_eligible",
        "active_delivery_elsewhere",
        "control_disabled",
        "render_failed",
    ]


def test_ingress_disposition_values_are_exact() -> None:
    assert [disposition.value for disposition in IngressDisposition] == [
        "accepted_command",
        "accepted_proxy_control",
        "rejected_disabled",
        "rejected_retained",
        "rejected_topic",
        "rejected_unknown_device",
        "rejected_device_mismatch",
        "rejected_oversize",
        "rejected_utf8",
        "rejected_json",
        "rejected_schema",
        "rejected_not_allowed",
        "rejected_value",
        "rejected_xml",
        "rejected_store",
    ]


def test_lifecycle_disposition_and_retry_values_are_exact() -> None:
    assert [reason.value for reason in RetryReason] == [
        "write_failed",
        "write_unknown",
        "disconnect",
        "ack_timeout",
        "unexpected_response",
        "stream_error",
        "shutdown",
    ]
    assert [disposition.value for disposition in EventDisposition] == [
        "confirmed",
        "unmatched",
        "duplicate",
    ]
    assert [disposition.value for disposition in LocalResponseDisposition] == [
        "ack_accepted",
        "next_sent",
        "nack_accepted",
        "duplicate",
        "rejected",
        "timed_out",
    ]


def test_twin_command_snapshot_is_frozen(command: TwinCommand) -> None:
    with pytest.raises(FrozenInstanceError):
        command.state = CommandState.CONFIRMED  # type: ignore[misc]


@pytest.mark.parametrize(
    ("record_type", "expected_fields"),
    [
        (
            TwinCommand,
            (
                "command_id",
                "audit_id",
                "device_id",
                "table_name",
                "item_name",
                "value_text",
                "raw_ingress_text",
                "state",
                "created_at_ms",
                "updated_at_ms",
                "pending_expires_at_ms",
                "wire_id",
                "wire_id_set",
                "wire_dt",
                "attempt_count",
                "active_session_id",
                "ack_deadline_ms",
                "event_deadline_ms",
                "acked_at_ms",
                "ack_device_rdt",
                "completed_at_ms",
                "predecessor_command_id",
                "last_wire_frame",
                "last_error",
            ),
        ),
        (
            CommandAttempt,
            (
                "command_id",
                "attempt_number",
                "session_id",
                "prepared_at_ms",
                "write_started_at_ms",
                "drain_completed_at_ms",
                "ack_deadline_ms",
                "tsec_text",
                "ver_text",
                "crc_text",
                "wire_frame",
                "wire_length",
                "write_outcome",
                "write_error",
                "response_fingerprint",
                "response_rdt",
            ),
        ),
        (
            CommandTransition,
            (
                "transition_id",
                "command_id",
                "audit_id",
                "from_state",
                "to_state",
                "occurred_at_ms",
                "attempt_number",
                "session_id",
                "reason",
                "error_text",
                "wire_frame",
                "evidence_frame",
            ),
        ),
        (
            AuditDeliveryDecision,
            (
                "transition_id",
                "audit_id",
                "command_id",
                "canonical_payload_sha256",
                "decision_integrity_sha256",
                "raw_bytes",
                "payload_capped",
                "state",
            ),
        ),
        (
            SettingEventReceipt,
            (
                "evidence_id",
                "received_at_ms",
                "device_id",
                "event_id_set",
                "device_dt",
                "table_name",
                "item_name",
                "old_value_text",
                "new_value_text",
                "evidence_frame",
                "disposition",
                "command_id",
                "duplicate_count",
                "last_seen_at_ms",
            ),
        ),
        (
            ControlIngress,
            (
                "ingress_id",
                "received_at_ms",
                "topic",
                "topic_device_id",
                "retain",
                "raw_text",
                "disposition",
                "reason",
                "command_id",
                "audit_id",
            ),
        ),
        (
            DeviceState,
            (
                "device_id",
                "first_seen_at_ms",
                "last_seen_at_ms",
                "next_wire_id",
                "next_wire_id_set",
            ),
        ),
        (
            PragmaSnapshot,
            ("journal_mode", "synchronous", "foreign_keys", "busy_timeout_ms"),
        ),
        (
            RecoveryReport,
            (
                "expired_pending",
                "retry_pending",
                "failed_attempt_limit",
                "kept_awaiting_event",
                "incomplete_event_timeout",
            ),
        ),
        (
            StoreStatus,
            (
                "state_counts",
                "nonterminal_commands",
                "control_available",
                "degradation_reason",
            ),
        ),
        (
            ConfirmedSetting,
            (
                "command_id",
                "audit_id",
                "evidence_id",
                "device_id",
                "table_name",
                "item_name",
                "value_text",
                "confirmed_at_ms",
            ),
        ),
        (
            TransitionAuditSnapshot,
            ("command", "transition", "attempt", "evidence"),
        ),
        (
            EnqueueResult,
            ("command", "superseded_command", "snapshots"),
        ),
        (
            ClaimResult,
            ("disposition", "command", "attempt", "snapshots"),
        ),
        (
            AckResult,
            ("accepted_command", "duplicate", "next_claim", "snapshots"),
        ),
        (
            NackResult,
            ("accepted_command", "duplicate", "snapshots"),
        ),
        (
            EventMatchResult,
            (
                "disposition",
                "command",
                "prior_state",
                "active_session_id",
                "evidence",
                "confirmation",
                "snapshot",
            ),
        ),
        (
            EventTimeoutCandidate,
            (
                "command_id",
                "device_id",
                "table_name",
                "item_name",
                "value_text",
                "acked_at_ms",
                "ack_device_rdt",
                "event_deadline_ms",
            ),
        ),
        (
            SweepReport,
            (
                "expired_pending",
                "retry_pending",
                "failed_attempt_limit",
                "incomplete_event_timeout",
                "snapshots",
            ),
        ),
    ],
)
def test_shared_records_are_frozen_slotted_and_have_exact_fields(
    record_type: type[Any], expected_fields: tuple[str, ...]
) -> None:
    assert tuple(field.name for field in fields(record_type)) == expected_fields
    assert record_type.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert "__dict__" not in record_type.__dict__


def test_render_contracts_are_frozen_and_exact(command: TwinCommand) -> None:
    context = AttemptRenderContext(
        command=command,
        attempt_number=1,
        prepared_at_ms=10,
        wire_id=101,
        wire_id_set=202,
        wire_dt="2026-08-07 12:00:00",
        used_ver_texts=("00001",),
    )
    rendered = RenderedAttempt(
        tsec_text="1234567890",
        ver_text="00002",
        crc_text="12345",
        wire_frame=b"frame",
    )

    assert context.command is command
    assert context.used_ver_texts == ("00001",)
    assert rendered.wire_frame == b"frame"
    with pytest.raises(FrozenInstanceError):
        context.attempt_number = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        rendered.crc_text = "54321"  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"ack_timeout_ms": -1},
        {"event_timeout_ms": -1},
        {"pending_ttl_ms": -1},
        {"max_attempts": 0},
        {"max_attempts": 9},
    ],
)
def test_control_policy_rejects_out_of_range_values(overrides: dict[str, int]) -> None:
    values = {
        "ack_timeout_ms": 1,
        "event_timeout_ms": 2,
        "pending_ttl_ms": 3,
        "max_attempts": 4,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        ControlPolicy(**values)


def test_control_policy_accepts_zero_time_limits_and_attempt_boundaries() -> None:
    assert ControlPolicy(0, 0, 0, 1).max_attempts == 1
    assert ControlPolicy(0, 0, 0, 8).max_attempts == 8


def test_control_ingress_supports_unpersisted_six_field_envelope() -> None:
    ingress = ControlIngress(
        "ing-1",
        110,
        "oig/123/control/set",
        "123",
        False,
        '{"value":2}',
    )

    assert ingress.disposition is None
    assert ingress.reason is None
    assert ingress.command_id is None
    assert ingress.audit_id is None


@pytest.mark.parametrize("value", [True, 1.5, "1"])
def test_control_policy_rejects_non_integer_values(value: object) -> None:
    with pytest.raises(TypeError):
        ControlPolicy(
            ack_timeout_ms=value,  # type: ignore[arg-type]
            event_timeout_ms=0,
            pending_ttl_ms=0,
            max_attempts=1,
        )


def test_store_status_requires_exact_immutable_state_counts() -> None:
    counts = tuple((state, index) for index, state in enumerate(CommandState))
    status = StoreStatus(
        state_counts=counts,
        nonterminal_commands=6,
        control_available=True,
        degradation_reason=None,
    )

    assert status.state_counts == counts
    assert status.count(CommandState.AWAITING_ACK) == 2
    with pytest.raises(ValueError):
        StoreStatus(
            state_counts=counts[:-1],
            nonterminal_commands=6,
            control_available=True,
            degradation_reason=None,
        )
    with pytest.raises(ValueError):
        StoreStatus(
            state_counts=counts,
            nonterminal_commands=6,
            control_available=False,
            degradation_reason="x" * 1025,
        )


def test_store_status_rejects_negative_or_inconsistent_counts() -> None:
    counts = tuple((state, 0) for state in CommandState)
    negative_counts = (
        (CommandState.PENDING, -1),
        *counts[1:],
    )

    with pytest.raises(ValueError):
        StoreStatus(negative_counts, 0, True, None)
    with pytest.raises(ValueError):
        StoreStatus(counts, -1, True, None)
    with pytest.raises(ValueError):
        StoreStatus(counts, 1, True, None)

    status = StoreStatus(counts, 0, True, None)
    with pytest.raises(ValueError):
        status.count("pending")  # type: ignore[arg-type]
