"""Behavioral and SQLite artifact tests for the durable twin store."""

# pyright: reportMissingImports=false
# pylint: disable=import-error,missing-function-docstring,too-many-lines

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from collections.abc import Callable
from typing import Any, cast, Iterator, Literal

import pytest

from twin.state import (
    AttemptRenderContext,
    AttemptRenderer,
    AttemptWriteOutcome,
    ClaimDisposition,
    CommandState,
    ControlIngress,
    ControlPolicy,
    DeviceState,
    IngressDisposition,
    PragmaSnapshot,
    RenderedAttempt,
    RetryReason,
)
from twin.ack_parser import SettingResponse
from twin.store import (
    CorruptStoreError,
    MigrationError,
    StoreRecordNotFound,
    StaleAttemptError,
    StoreLockError,
    TwinCommandStore,
    TwinStoreError,
    UnsupportedSchemaError,
)
import twin.store as store_module


_MAX_SQLITE_INTEGER = (1 << 63) - 1
_EXPECTED_TABLES = {
    "schema_meta",
    "devices",
    "commands",
    "control_ingress_audit",
    "command_attempts",
    "command_transitions",
    "event_receipts",
}
_EXPECTED_INDEXES = {
    "idx_commands_fifo",
    "idx_commands_event_match",
    "idx_commands_predecessor",
    "ux_commands_one_awaiting_ack_per_device",
    "ux_commands_one_unsent_successor_per_target",
    "ux_event_receipts_one_confirmation_per_command",
}
_EXPECTED_COLUMN_ARTIFACTS = {
    "schema_meta": (
        ("schema_version", "INTEGER", 0, None, 1),
        ("created_at_ms", "INTEGER", 1, None, 0),
    ),
    "devices": (
        ("device_id", "TEXT", 0, None, 1),
        ("first_seen_at_ms", "INTEGER", 1, None, 0),
        ("last_seen_at_ms", "INTEGER", 1, None, 0),
        ("next_wire_id", "INTEGER", 1, None, 0),
        ("next_wire_id_set", "INTEGER", 1, None, 0),
    ),
    "commands": (
        ("command_id", "TEXT", 0, None, 1),
        ("audit_id", "TEXT", 1, None, 0),
        ("device_id", "TEXT", 1, None, 0),
        ("table_name", "TEXT", 1, None, 0),
        ("item_name", "TEXT", 1, None, 0),
        ("value_text", "TEXT", 1, None, 0),
        ("raw_ingress_text", "TEXT", 1, None, 0),
        ("state", "TEXT", 1, None, 0),
        ("created_at_ms", "INTEGER", 1, None, 0),
        ("updated_at_ms", "INTEGER", 1, None, 0),
        ("pending_expires_at_ms", "INTEGER", 1, None, 0),
        ("wire_id", "INTEGER", 0, None, 0),
        ("wire_id_set", "INTEGER", 0, None, 0),
        ("wire_dt", "TEXT", 0, None, 0),
        ("attempt_count", "INTEGER", 1, "0", 0),
        ("active_session_id", "TEXT", 0, None, 0),
        ("ack_deadline_ms", "INTEGER", 0, None, 0),
        ("event_deadline_ms", "INTEGER", 0, None, 0),
        ("acked_at_ms", "INTEGER", 0, None, 0),
        ("ack_device_rdt", "TEXT", 0, None, 0),
        ("completed_at_ms", "INTEGER", 0, None, 0),
        ("predecessor_command_id", "TEXT", 0, None, 0),
        ("last_wire_frame", "BLOB", 0, None, 0),
        ("last_error", "TEXT", 0, None, 0),
    ),
    "control_ingress_audit": (
        ("ingress_id", "TEXT", 0, None, 1),
        ("received_at_ms", "INTEGER", 1, None, 0),
        ("topic", "TEXT", 1, None, 0),
        ("topic_device_id", "TEXT", 0, None, 0),
        ("retain", "INTEGER", 1, None, 0),
        ("disposition", "TEXT", 1, None, 0),
        ("reason", "TEXT", 1, None, 0),
        ("raw_text", "TEXT", 1, None, 0),
        ("command_id", "TEXT", 0, None, 0),
        ("audit_id", "TEXT", 0, None, 0),
    ),
    "command_attempts": (
        ("command_id", "TEXT", 1, None, 1),
        ("attempt_number", "INTEGER", 1, None, 2),
        ("session_id", "TEXT", 1, None, 0),
        ("prepared_at_ms", "INTEGER", 1, None, 0),
        ("write_started_at_ms", "INTEGER", 0, None, 0),
        ("drain_completed_at_ms", "INTEGER", 0, None, 0),
        ("ack_deadline_ms", "INTEGER", 1, None, 0),
        ("tsec_text", "TEXT", 1, None, 0),
        ("ver_text", "TEXT", 1, None, 0),
        ("crc_text", "TEXT", 1, None, 0),
        ("wire_frame", "BLOB", 1, None, 0),
        ("wire_length", "INTEGER", 1, None, 0),
        ("write_outcome", "TEXT", 1, None, 0),
        ("write_error", "TEXT", 0, None, 0),
        ("response_fingerprint", "TEXT", 0, None, 0),
        ("response_rdt", "TEXT", 0, None, 0),
    ),
    "command_transitions": (
        ("transition_id", "INTEGER", 0, None, 1),
        ("command_id", "TEXT", 1, None, 0),
        ("audit_id", "TEXT", 1, None, 0),
        ("from_state", "TEXT", 0, None, 0),
        ("to_state", "TEXT", 1, None, 0),
        ("occurred_at_ms", "INTEGER", 1, None, 0),
        ("attempt_number", "INTEGER", 0, None, 0),
        ("session_id", "TEXT", 0, None, 0),
        ("reason", "TEXT", 1, None, 0),
        ("error_text", "TEXT", 0, None, 0),
        ("wire_frame", "BLOB", 0, None, 0),
        ("evidence_frame", "BLOB", 0, None, 0),
    ),
    "event_receipts": (
        ("evidence_id", "TEXT", 0, None, 1),
        ("received_at_ms", "INTEGER", 1, None, 0),
        ("device_id", "TEXT", 1, None, 0),
        ("event_id_set", "INTEGER", 1, None, 0),
        ("device_dt", "TEXT", 1, None, 0),
        ("table_name", "TEXT", 1, None, 0),
        ("item_name", "TEXT", 1, None, 0),
        ("old_value_text", "TEXT", 1, None, 0),
        ("new_value_text", "TEXT", 1, None, 0),
        ("evidence_frame", "BLOB", 1, None, 0),
        ("disposition", "TEXT", 1, None, 0),
        ("command_id", "TEXT", 0, None, 0),
        ("duplicate_count", "INTEGER", 1, "0", 0),
        ("last_seen_at_ms", "INTEGER", 1, None, 0),
    ),
}
_EXPECTED_FOREIGN_KEY_ARTIFACTS = {
    "schema_meta": frozenset(),
    "devices": frozenset(),
    "commands": frozenset(
        {
            (
                "commands",
                (("predecessor_command_id", "command_id"),),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
            (
                "devices",
                (("device_id", "device_id"),),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
        }
    ),
    "control_ingress_audit": frozenset(
        {
            (
                "commands",
                (("command_id", "command_id"), ("audit_id", "audit_id")),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            )
        }
    ),
    "command_attempts": frozenset(
        {
            (
                "commands",
                (("command_id", "command_id"),),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            )
        }
    ),
    "command_transitions": frozenset(
        {
            (
                "commands",
                (("command_id", "command_id"), ("audit_id", "audit_id")),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            )
        }
    ),
    "event_receipts": frozenset(
        {
            (
                "commands",
                (("command_id", "command_id"),),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
            (
                "devices",
                (("device_id", "device_id"),),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
        }
    ),
}
_EXPECTED_EXPLICIT_INDEX_ARTIFACTS = {
    "idx_commands_fifo": (
        "commands",
        0,
        0,
        ("device_id", "state", "created_at_ms", "command_id"),
        None,
    ),
    "idx_commands_event_match": (
        "commands",
        0,
        0,
        ("device_id", "table_name", "item_name", "value_text", "state", "acked_at_ms"),
        None,
    ),
    "idx_commands_predecessor": (
        "commands",
        0,
        0,
        ("predecessor_command_id",),
        None,
    ),
    "ux_commands_one_awaiting_ack_per_device": (
        "commands",
        1,
        1,
        ("device_id",),
        "state = 'awaiting_ack'",
    ),
    "ux_commands_one_unsent_successor_per_target": (
        "commands",
        1,
        1,
        ("device_id", "table_name", "item_name"),
        "state = 'pending' AND attempt_count = 0",
    ),
    "ux_event_receipts_one_confirmation_per_command": (
        "event_receipts",
        1,
        1,
        ("command_id",),
        "command_id IS NOT NULL",
    ),
}
_EXPECTED_AUTOMATIC_INDEX_ARTIFACTS = {
    "schema_meta": frozenset(),
    "devices": frozenset({("pk", 1, 0, ("device_id",))}),
    "commands": frozenset(
        {
            ("pk", 1, 0, ("command_id",)),
            ("u", 1, 0, ("audit_id",)),
            ("u", 1, 0, ("command_id", "audit_id")),
        }
    ),
    "control_ingress_audit": frozenset({("pk", 1, 0, ("ingress_id",))}),
    "command_attempts": frozenset(
        {("pk", 1, 0, ("command_id", "attempt_number"))}
    ),
    "command_transitions": frozenset(),
    "event_receipts": frozenset({("pk", 1, 0, ("evidence_id",))}),
}
_EXPECTED_CHECK_FRAGMENTS = {
    "schema_meta": (
        "CHECK (schema_version >= 1)",
        "CHECK (created_at_ms >= 0)",
    ),
    "devices": (
        "CHECK (length(device_id) BETWEEN 1 AND 128)",
        "CHECK (first_seen_at_ms >= 0)",
        "CHECK (last_seen_at_ms >= first_seen_at_ms)",
        "CHECK (next_wire_id >= 0)",
        "CHECK (next_wire_id_set >= 0)",
    ),
    "commands": (
        "CHECK (length(table_name) BETWEEN 1 AND 128)",
        "CHECK (length(item_name) BETWEEN 1 AND 128)",
        "CHECK (length(value_text) BETWEEN 1 AND 1024)",
        "CHECK (length(raw_ingress_text) <= 16384)",
        (
            "CHECK (state IN ( 'pending','retry_pending','awaiting_ack','awaiting_event', "
            "'confirmed','incomplete','failed','expired','superseded' ))"
        ),
        "CHECK (created_at_ms >= 0)",
        "CHECK (updated_at_ms >= created_at_ms)",
        "CHECK (pending_expires_at_ms >= created_at_ms)",
        "CHECK (attempt_count BETWEEN 0 AND 8)",
        "CHECK (last_wire_frame IS NULL OR length(last_wire_frame) <= 1048576)",
        "CHECK (last_error IS NULL OR length(last_error) <= 1024)",
        "CHECK (predecessor_command_id IS NULL OR predecessor_command_id <> command_id)",
        (
            "CHECK ((wire_id IS NULL AND wire_id_set IS NULL AND wire_dt IS NULL) "
            "OR (wire_id IS NOT NULL AND wire_id_set IS NOT NULL "
            "AND wire_dt IS NOT NULL))"
        ),
    ),
    "control_ingress_audit": (
        "CHECK (received_at_ms >= 0)",
        "CHECK (length(topic) <= 1024)",
        "CHECK (topic_device_id IS NULL OR length(topic_device_id) <= 128)",
        "CHECK (retain IN (0, 1))",
        "CHECK (length(reason) <= 1024)",
        "CHECK (length(raw_text) <= 16384)",
        (
            "CHECK ((command_id IS NULL AND audit_id IS NULL) OR "
            "(command_id IS NOT NULL AND audit_id IS NOT NULL))"
        ),
    ),
    "command_attempts": (
        "CHECK (attempt_number BETWEEN 1 AND 8)",
        "CHECK (length(session_id) BETWEEN 1 AND 128)",
        "CHECK (prepared_at_ms >= 0)",
        "CHECK (ack_deadline_ms >= prepared_at_ms)",
        "CHECK (length(ver_text) = 5)",
        "CHECK (length(crc_text) = 5)",
        "CHECK (length(wire_frame) <= 1048576)",
        "CHECK (wire_length = length(wire_frame))",
        "CHECK ( write_outcome IN ('prepared','started','drained','unknown','failed') )",
        "CHECK (write_error IS NULL OR length(write_error) <= 1024)",
        "CHECK ( response_fingerprint IS NULL OR length(response_fingerprint) = 64 )",
    ),
    "command_transitions": (
        "CHECK (occurred_at_ms >= 0)",
        "CHECK (length(reason) <= 1024)",
        "CHECK (error_text IS NULL OR length(error_text) <= 1024)",
        "CHECK (wire_frame IS NULL OR length(wire_frame) <= 1048576)",
        "CHECK (evidence_frame IS NULL OR length(evidence_frame) <= 1048576)",
    ),
    "event_receipts": (
        "CHECK (length(evidence_id) = 64)",
        "CHECK (received_at_ms >= 0)",
        "CHECK (event_id_set >= 0)",
        "CHECK (length(table_name) BETWEEN 1 AND 128)",
        "CHECK (length(item_name) BETWEEN 1 AND 128)",
        "CHECK (length(old_value_text) <= 1024)",
        "CHECK (length(new_value_text) <= 1024)",
        "CHECK (length(evidence_frame) <= 1048576)",
        "CHECK (disposition IN ('confirmed','unmatched'))",
        "CHECK (duplicate_count >= 0)",
        "CHECK (last_seen_at_ms >= received_at_ms)",
    ),
}


class _CloseFaultConnection:
    """Connection proxy that fails a bounded number of close attempts."""

    def __init__(
        self, connection: sqlite3.Connection, *, close_failures: int = 1
    ) -> None:
        self._connection = connection
        self.close_failures = close_failures
        self.close_attempts = 0
        self.closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def close(self) -> None:
        self.close_attempts += 1
        if self.close_failures:
            self.close_failures -= 1
            raise sqlite3.OperationalError("forced SQLite close failure")
        self._connection.close()
        self.closed = True

    def force_close(self) -> None:
        """Close the delegated connection during failed-test cleanup."""
        if not self.closed:
            self._connection.close()
            self.closed = True


class _RollbackFaultConnection:
    """Connection proxy that fails rollback while delegating other SQL."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self.rollback_attempts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def execute(self, statement: str, parameters: Any = ()) -> Any:
        if statement.strip().upper() == "ROLLBACK":
            self.rollback_attempts += 1
            raise sqlite3.OperationalError("forced rollback failure")
        return self._connection.execute(statement, parameters)

    def close(self) -> None:
        self._connection.close()


class _ObservationBaseExceptionConnection:
    """Connection proxy that raises after BEGIN and can fail rollback."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        operation_error: BaseException,
        rollback_error: BaseException | None = None,
    ) -> None:
        self._connection = connection
        self.operation_error: BaseException | None = operation_error
        self.rollback_error = rollback_error
        self.rollback_attempts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def execute(self, statement: str, parameters: Any = ()) -> Any:
        normalized = " ".join(statement.split()).upper()
        if normalized.startswith("SELECT FIRST_SEEN_AT_MS"):
            operation_error = self.operation_error
            self.operation_error = None
            if operation_error is not None:
                raise operation_error
        if normalized == "ROLLBACK":
            self.rollback_attempts += 1
            if self.rollback_error is not None:
                raise self.rollback_error
        return self._connection.execute(statement, parameters)

    def close(self) -> None:
        self._connection.close()


class _TransitionInsertFaultConnection:
    """Connection proxy that fails one transition insert after BEGIN."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self.fail_next_transition = True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def execute(self, statement: str, parameters: Any = ()) -> Any:
        normalized = " ".join(statement.split()).upper()
        if (
            self.fail_next_transition
            and normalized.startswith("INSERT INTO COMMAND_TRANSITIONS")
        ):
            self.fail_next_transition = False
            raise sqlite3.OperationalError("forced transition insert failure")
        return self._connection.execute(statement, parameters)

    def close(self) -> None:
        self._connection.close()


class _LifecycleFaultConnection:
    """Inject one lifecycle fault and optionally fail the required rollback."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        operation_error: BaseException,
        rollback_error: BaseException | None = None,
    ) -> None:
        self._connection = connection
        self.operation_error: BaseException | None = operation_error
        self.rollback_error = rollback_error
        self.rollback_attempts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def execute(self, statement: str, parameters: Any = ()) -> Any:
        normalized = " ".join(statement.split()).upper()
        if (
            self.operation_error is not None
            and normalized.startswith("INSERT INTO COMMAND_TRANSITIONS")
        ):
            error = self.operation_error
            self.operation_error = None
            raise error
        if normalized == "ROLLBACK":
            self.rollback_attempts += 1
            if self.rollback_error is not None:
                raise self.rollback_error
        return self._connection.execute(statement, parameters)

    def close(self) -> None:
        self._connection.close()


class _CommitOutcomeFaultConnection:  # pylint: disable=too-few-public-methods
    """Commit successfully, then report an ambiguous caller-visible failure."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self.commit_attempts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def execute(self, statement: str, parameters: Any = ()) -> Any:
        if statement.strip().upper() == "COMMIT":
            self.commit_attempts += 1
            self._connection.execute(statement, parameters)
            raise sqlite3.OperationalError("forced post-commit reporting failure")
        return self._connection.execute(statement, parameters)

    def close(self) -> None:
        self._connection.close()


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
    finally:
        connection.close()


def _schema_objects(path: Path, object_type: str) -> set[str]:
    with _connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
            (object_type,),
        ).fetchall()
    return {str(row[0]) for row in rows}


def _normalize_artifact_sql(statement: str) -> str:
    return " ".join(statement.rstrip().rstrip(";").split())


def _foreign_key_artifacts(
    connection: sqlite3.Connection, table_name: str
) -> frozenset[tuple[str, tuple[tuple[str, str], ...], str, str, str]]:
    grouped: dict[
        int,
        tuple[str, list[tuple[int, str, str]], str, str, str],
    ] = {}
    for row in connection.execute(
        f"PRAGMA foreign_key_list({table_name})"
    ).fetchall():
        foreign_key_id = int(row[0])
        if foreign_key_id not in grouped:
            grouped[foreign_key_id] = (
                str(row[2]),
                [],
                str(row[5]),
                str(row[6]),
                str(row[7]),
            )
        grouped[foreign_key_id][1].append(
            (int(row[1]), str(row[3]), str(row[4]))
        )
    return frozenset(
        (
            target,
            tuple(
                (source_column, target_column)
                for _, source_column, target_column in sorted(columns)
            ),
            on_update,
            on_delete,
            match,
        )
        for target, columns, on_update, on_delete, match in grouped.values()
    )


def _create_schema_meta_only(path: Path, version: int) -> None:
    with _connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_meta (
                schema_version INTEGER PRIMARY KEY CHECK (schema_version >= 1),
                created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_meta(schema_version, created_at_ms) VALUES (?, ?)",
            (version, 123),
        )


def _create_unconstrained_schema_meta(
    path: Path, rows: list[tuple[object, object]]
) -> None:
    with _connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_meta(schema_version, created_at_ms)"
        )
        connection.executemany(
            "INSERT INTO schema_meta(schema_version, created_at_ms) VALUES (?, ?)",
            rows,
        )


def _insert_minimal_command(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO devices(
            device_id, first_seen_at_ms, last_seen_at_ms,
            next_wire_id, next_wire_id_set
        ) VALUES ('device-1', 1, 1, 2, 2)
        """
    )
    connection.execute(
        """
        INSERT INTO commands(
            command_id, audit_id, device_id, table_name, item_name,
            value_text, raw_ingress_text, state, created_at_ms,
            updated_at_ms, pending_expires_at_ms
        ) VALUES (
            'command-1', 'audit-1', 'device-1', 'tbl_set', 'T_Room',
            '22', '{}', 'pending', 1, 1, 100
        )
        """
    )


def test_open_creates_schema_v1_and_repeated_open_is_idempotent(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    first = TwinCommandStore(path, policy=control_policy)
    first.open(now_ms=1000)
    assert first.schema_version == 1
    assert first.schema_created_at_ms == 1000
    assert first.policy is control_policy
    first.close()

    first_bytes = path.read_bytes()
    second = TwinCommandStore(path, policy=control_policy)
    second.open(now_ms=2000)
    assert second.schema_version == 1
    assert second.schema_created_at_ms == 1000
    second.close()

    assert path.read_bytes() == first_bytes


def test_constructor_rejects_non_policy(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        TwinCommandStore(tmp_path / "twin.db", policy=object())  # type: ignore[arg-type]


def test_open_rejects_second_open_on_same_owner(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)
    store.open(now_ms=1)

    with pytest.raises(TwinStoreError):
        store.open(now_ms=2)

    store.close()


@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (sqlite3.DatabaseError("forced SQLite failure"), CorruptStoreError),
        (OSError("forced filesystem failure"), TwinStoreError),
    ],
)
def test_open_releases_lock_and_preserves_artifacts_on_setup_failure(
    tmp_path: Path,
    control_policy: ControlPolicy,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    expected_error: type[BaseException],
) -> None:
    path = tmp_path / "twin.db"

    def fail_pragmas(_connection: sqlite3.Connection) -> None:
        raise error

    monkeypatch.setattr(
        TwinCommandStore,
        "_configure_pragmas",
        staticmethod(fail_pragmas),
    )
    store = TwinCommandStore(path, policy=control_policy)

    with pytest.raises(expected_error):
        store.open(now_ms=1)

    assert path.exists()
    assert Path(f"{path}.lock").exists()
    assert not store.is_open


def test_schema_v1_has_exact_tables_indexes_and_composite_identity_foreign_keys(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    store.close()

    assert _schema_objects(path, "table") == _EXPECTED_TABLES
    assert _schema_objects(path, "index") == _EXPECTED_INDEXES

    with _connect(path) as connection:
        command_columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(commands)")
        )
        assert command_columns == (
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
        )

        ingress_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(control_ingress_audit)"
        ).fetchall()
        transition_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(command_transitions)"
        ).fetchall()
        assert {(row[2], row[3], row[4]) for row in ingress_foreign_keys} == {
            ("commands", "command_id", "command_id"),
            ("commands", "audit_id", "audit_id"),
        }
        assert {(row[2], row[3], row[4]) for row in transition_foreign_keys} == {
            ("commands", "command_id", "command_id"),
            ("commands", "audit_id", "audit_id"),
        }


# pylint: disable-next=too-many-locals
def test_schema_v1_complete_independent_sqlite_artifact_contract(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    store.close()

    with _connect(path) as connection:
        persistent_objects = {
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                """
                SELECT type, name FROM sqlite_schema
                WHERE name NOT LIKE 'sqlite_%'
                """
            ).fetchall()
        }
        assert persistent_objects == {
            *(("table", name) for name in _EXPECTED_TABLES),
            *(("index", name) for name in _EXPECTED_INDEXES),
        }

        explicit_indexes: dict[
            str, tuple[str, int, int, tuple[str, ...], str | None]
        ] = {}
        for table_name, expected_columns in _EXPECTED_COLUMN_ARTIFACTS.items():
            columns = tuple(
                (str(row[1]), str(row[2]), int(row[3]), row[4], int(row[5]))
                for row in connection.execute(
                    f"PRAGMA table_info({table_name})"
                ).fetchall()
            )
            assert columns == expected_columns
            assert (
                _foreign_key_artifacts(connection, table_name)
                == _EXPECTED_FOREIGN_KEY_ARTIFACTS[table_name]
            )

            automatic_indexes = set()
            for index_row in connection.execute(
                f"PRAGMA index_list({table_name})"
            ).fetchall():
                index_name = str(index_row[1])
                unique = int(index_row[2])
                origin = str(index_row[3])
                partial = int(index_row[4])
                index_columns = tuple(
                    str(row[2])
                    for row in connection.execute(
                        f"PRAGMA index_info({index_name})"
                    ).fetchall()
                )
                if index_name.startswith("sqlite_"):
                    automatic_indexes.add(
                        (origin, unique, partial, index_columns)
                    )
                    continue
                index_sql = str(
                    connection.execute(
                        "SELECT sql FROM sqlite_schema WHERE name = ?",
                        (index_name,),
                    ).fetchone()[0]
                )
                normalized_index_sql = _normalize_artifact_sql(index_sql)
                predicate = (
                    normalized_index_sql.split(" WHERE ", 1)[1]
                    if " WHERE " in normalized_index_sql
                    else None
                )
                explicit_indexes[index_name] = (
                    table_name,
                    unique,
                    partial,
                    index_columns,
                    predicate,
                )
            assert frozenset(automatic_indexes) == (
                _EXPECTED_AUTOMATIC_INDEX_ARTIFACTS[table_name]
            )

            table_sql = str(
                connection.execute(
                    "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?",
                    (table_name,),
                ).fetchone()[0]
            )
            normalized_table_sql = _normalize_artifact_sql(table_sql)
            for constraint in _EXPECTED_CHECK_FRAGMENTS[table_name]:
                assert _normalize_artifact_sql(constraint) in normalized_table_sql

        assert explicit_indexes == _EXPECTED_EXPLICIT_INDEX_ARTIFACTS


def test_schema_v1_transition_ids_are_never_reused_after_highest_delete(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    store.close()

    with _connect(path) as connection:
        _insert_minimal_command(connection)
        first_cursor = connection.execute(
            """
            INSERT INTO command_transitions(
                command_id, audit_id, from_state, to_state,
                occurred_at_ms, reason
            ) VALUES ('command-1', 'audit-1', NULL, 'pending', 1, 'created')
            """
        )
        first_transition_id = int(first_cursor.lastrowid or 0)
        connection.execute(
            "DELETE FROM command_transitions WHERE transition_id = ?",
            (first_transition_id,),
        )
        second_cursor = connection.execute(
            """
            INSERT INTO command_transitions(
                command_id, audit_id, from_state, to_state,
                occurred_at_ms, reason
            ) VALUES ('command-1', 'audit-1', NULL, 'pending', 2, 'recreated')
            """
        )
        second_transition_id = int(second_cursor.lastrowid or 0)

    assert second_transition_id > first_transition_id


def test_schema_v1_partial_unique_indexes_enforce_their_predicates(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    store.close()

    with _connect(path) as connection:
        _insert_minimal_command(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO commands(
                    command_id, audit_id, device_id, table_name, item_name,
                    value_text, raw_ingress_text, state, created_at_ms,
                    updated_at_ms, pending_expires_at_ms
                ) VALUES (
                    'command-2', 'audit-2', 'device-1', 'tbl_set', 'T_Room',
                    '23', '{}', 'pending', 2, 2, 100
                )
                """
            )
        connection.execute(
            "UPDATE commands SET state = 'awaiting_ack' WHERE command_id = 'command-1'"
        )
        connection.execute(
            """
            INSERT INTO commands(
                command_id, audit_id, device_id, table_name, item_name,
                value_text, raw_ingress_text, state, created_at_ms,
                updated_at_ms, pending_expires_at_ms
            ) VALUES (
                'command-2', 'audit-2', 'device-1', 'tbl_set', 'T_Mode',
                'AUTO', '{}', 'pending', 2, 2, 100
            )
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE commands SET state = 'awaiting_ack' WHERE command_id = 'command-2'"
            )
        connection.execute(
            """
            INSERT INTO event_receipts(
                evidence_id, received_at_ms, device_id, event_id_set,
                device_dt, table_name, item_name, old_value_text,
                new_value_text, evidence_frame, disposition, command_id,
                last_seen_at_ms
            ) VALUES (?, 1, 'device-1', 1, 'dt', 'tbl_set', 'T_Room',
                '21', '22', X'01', 'confirmed', 'command-1', 1)
            """,
            ("a" * 64,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO event_receipts(
                    evidence_id, received_at_ms, device_id, event_id_set,
                    device_dt, table_name, item_name, old_value_text,
                    new_value_text, evidence_frame, disposition, command_id,
                    last_seen_at_ms
                ) VALUES (?, 2, 'device-1', 2, 'dt', 'tbl_set', 'T_Room',
                    '21', '22', X'02', 'confirmed', 'command-1', 2)
                """,
                ("b" * 64,),
            )


def test_schema_v1_enforces_core_state_wire_attempt_and_event_checks(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    store.close()

    with _connect(path) as connection:
        _insert_minimal_command(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE commands SET state = 'not-a-state' WHERE command_id = 'command-1'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE commands SET attempt_count = 9 WHERE command_id = 'command-1'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE commands SET wire_id = 1 WHERE command_id = 'command-1'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO command_attempts(
                    command_id, attempt_number, session_id, prepared_at_ms,
                    ack_deadline_ms, tsec_text, ver_text, crc_text, wire_frame,
                    wire_length, write_outcome
                ) VALUES (
                    'command-1', 1, 'session-1', 1, 2, '1', '00001', '00002',
                    X'01', 2, 'prepared'
                )
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO command_attempts(
                    command_id, attempt_number, session_id, prepared_at_ms,
                    ack_deadline_ms, tsec_text, ver_text, crc_text, wire_frame,
                    wire_length, write_outcome
                ) VALUES (
                    'command-1', 1, 'session-1', 1, 2, '1', '00001', '00002',
                    X'01', 1, 'not-an-outcome'
                )
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO event_receipts(
                    evidence_id, received_at_ms, device_id, event_id_set,
                    device_dt, table_name, item_name, old_value_text,
                    new_value_text, evidence_frame, disposition, last_seen_at_ms
                ) VALUES (
                    ?, 1, 'device-1', 1, 'dt', 'tbl_set', 'T_Room', '21', '22',
                    X'01', 'not-a-disposition', 1
                )
                """,
                ("a" * 64,),
            )


def test_open_enables_required_pragmas(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)
    store.open(now_ms=1)
    expected = PragmaSnapshot(
        journal_mode="wal",
        synchronous=2,
        foreign_keys=1,
        busy_timeout_ms=5000,
    )
    assert store.pragma_snapshot() == expected
    assert store.verify_health() == expected
    store.close()


def test_verify_health_rejects_changed_connection_pragma(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)
    store.open(now_ms=1)
    assert store._connection is not None  # pylint: disable=protected-access
    store._connection.execute("PRAGMA foreign_keys=OFF")  # pylint: disable=protected-access

    with pytest.raises(TwinStoreError):
        store.verify_health()

    store.close()


def test_open_holds_exclusive_process_lock_until_close(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    owner = TwinCommandStore(path, policy=control_policy)
    owner.open(now_ms=1)
    contender = TwinCommandStore(path, policy=control_policy)
    database_before = path.read_bytes()
    lock_path = Path(f"{path}.lock")
    lock_inode = lock_path.stat().st_ino

    with pytest.raises(StoreLockError):
        contender.open(now_ms=2)

    assert path.read_bytes() == database_before
    assert lock_path.stat().st_ino == lock_inode
    owner.close()
    contender.open(now_ms=3)
    contender.close()
    assert lock_path.stat().st_ino == lock_inode


def test_open_detects_lock_path_identity_change_during_acquisition(
    tmp_path: Path,
    control_policy: ControlPolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "twin.db"
    lock_path = Path(f"{path}.lock")
    real_stat = os.stat

    def mismatched_lock_stat(
        target: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = real_stat(target, *args, **kwargs)
        if not isinstance(target, int) and Path(target) == lock_path:
            return SimpleNamespace(st_dev=result.st_dev, st_ino=result.st_ino + 1)
        return result

    monkeypatch.setattr(store_module.os, "stat", mismatched_lock_stat)
    store = TwinCommandStore(path, policy=control_policy)

    with pytest.raises(StoreLockError):
        store.open(now_ms=1)

    monkeypatch.setattr(store_module.os, "stat", real_stat)
    assert lock_path.exists()
    store.open(now_ms=2)
    store.close()


def test_close_is_idempotent_and_keeps_lock_artifact(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    lock_path = Path(f"{path}.lock")
    inode = lock_path.stat().st_ino

    store.close()
    store.close()

    assert not store.is_open
    assert lock_path.stat().st_ino == inode


def test_close_failure_retains_connection_and_lock_until_successful_retry(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    owner = TwinCommandStore(path, policy=control_policy)
    owner.open(now_ms=1)
    assert owner._connection is not None  # pylint: disable=protected-access
    connection = _CloseFaultConnection(
        owner._connection  # pylint: disable=protected-access
    )
    owner._connection = cast(  # pylint: disable=protected-access
        sqlite3.Connection, connection
    )
    contender = TwinCommandStore(path, policy=control_policy)

    try:
        with pytest.raises(TwinStoreError) as close_error:
            owner.close()

        assert len(str(close_error.value)) <= 1024
        assert owner.is_open
        assert owner._connection is connection  # pylint: disable=protected-access
        assert owner._process_lock is not None  # pylint: disable=protected-access
        with pytest.raises(TwinStoreError, match="close"):
            owner.verify_health()
        with pytest.raises(StoreLockError):
            contender.open(now_ms=2)

        owner.close()
        assert connection.close_attempts == 2
        assert connection.closed
        assert not owner.is_open
        contender.open(now_ms=3)
        contender.close()
    finally:
        try:
            owner.close()
        except (sqlite3.Error, TwinStoreError):
            pass
        contender.close()
        connection.force_close()


def test_failed_open_cleanup_retains_setup_and_close_failure_context(
    tmp_path: Path,
    control_policy: ControlPolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "twin.db"
    real_connect = sqlite3.connect
    real_configure = TwinCommandStore._configure_pragmas  # pylint: disable=protected-access
    delegated_connection: _CloseFaultConnection | None = None

    def wrapping_connect(database: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal delegated_connection
        raw_connection = real_connect(database, *args, **kwargs)
        delegated_connection = _CloseFaultConnection(raw_connection)
        return delegated_connection

    def fail_setup(_connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("forced setup failure")

    monkeypatch.setattr(store_module.sqlite3, "connect", wrapping_connect)
    monkeypatch.setattr(
        TwinCommandStore,
        "_configure_pragmas",
        staticmethod(fail_setup),
    )
    store = TwinCommandStore(path, policy=control_policy)
    contender = TwinCommandStore(path, policy=control_policy)

    try:
        with pytest.raises(TwinStoreError) as open_error:
            store.open(now_ms=1)

        assert delegated_connection is not None
        assert "setup failure" in str(open_error.value)
        assert "close failure" in str(open_error.value)
        assert len(str(open_error.value)) <= 1024
        assert store.is_open
        assert store._connection is delegated_connection  # pylint: disable=protected-access
        with pytest.raises(StoreLockError):
            contender.open(now_ms=2)

        store.close()
        assert delegated_connection.close_attempts == 2
        monkeypatch.setattr(store_module.sqlite3, "connect", real_connect)
        monkeypatch.setattr(
            TwinCommandStore,
            "_configure_pragmas",
            staticmethod(real_configure),
        )
        contender.open(now_ms=3)
        contender.close()
    finally:
        try:
            store.close()
        except (sqlite3.Error, TwinStoreError):
            pass
        contender.close()
        if delegated_connection is not None:
            delegated_connection.force_close()


def test_preflight_close_failure_retains_connection_and_lock_for_retry(
    tmp_path: Path,
    control_policy: ControlPolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "twin.db"
    creator = TwinCommandStore(path, policy=control_policy)
    creator.open(now_ms=1)
    creator.close()
    real_connect = sqlite3.connect
    delegated_connection: _CloseFaultConnection | None = None

    def wrapping_connect(database: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal delegated_connection
        raw_connection = real_connect(database, *args, **kwargs)
        if "mode=ro" not in str(database):
            return raw_connection
        delegated_connection = _CloseFaultConnection(raw_connection, close_failures=2)
        return delegated_connection

    monkeypatch.setattr(store_module.sqlite3, "connect", wrapping_connect)
    store = TwinCommandStore(path, policy=control_policy)
    contender = TwinCommandStore(path, policy=control_policy)

    try:
        with pytest.raises(TwinStoreError, match="cleanup failed"):
            store.open(now_ms=2)

        assert delegated_connection is not None
        assert delegated_connection.close_attempts == 2
        assert store.is_open
        with pytest.raises(StoreLockError):
            contender.open(now_ms=3)

        store.close()
        assert delegated_connection.closed
        monkeypatch.setattr(store_module.sqlite3, "connect", real_connect)
        contender.open(now_ms=4)
        contender.close()
    finally:
        try:
            store.close()
        except TwinStoreError:
            pass
        contender.close()
        if delegated_connection is not None:
            delegated_connection.force_close()


def test_verify_health_detects_lock_file_inode_replacement(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    lock_path = Path(f"{path}.lock")
    lock_path.unlink()
    replacement_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(replacement_fd)

    with pytest.raises(StoreLockError):
        store.verify_health()

    store.close()
    assert lock_path.exists()


def test_verify_health_detects_missing_lock_path(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    Path(f"{path}.lock").unlink()

    with pytest.raises(StoreLockError):
        store.verify_health()

    store.close()


def test_verify_health_detects_changed_descriptor_identity(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)
    store.open(now_ms=1)
    assert store._process_lock is not None  # pylint: disable=protected-access
    lock_fd, _ = store._process_lock  # pylint: disable=protected-access
    store._process_lock = (lock_fd, (0, 0))  # pylint: disable=protected-access

    with pytest.raises(StoreLockError):
        store.verify_health()

    store.close()


def test_verify_health_rejects_closed_store(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)

    with pytest.raises(TwinStoreError):
        store.verify_health()


def test_process_lock_verifier_rejects_missing_internal_ownership(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)

    with pytest.raises(StoreLockError):
        store._verify_process_lock()  # pylint: disable=protected-access


def test_corrupt_database_fails_closed_without_recreation(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    original = b"not a sqlite database\x00keep me"
    path.write_bytes(original)
    store = TwinCommandStore(path, policy=control_policy)

    with pytest.raises(CorruptStoreError):
        store.open(now_ms=1)

    assert path.read_bytes() == original
    assert Path(f"{path}.lock").exists()
    assert not store.is_open


def test_lock_open_failure_does_not_create_database(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "missing-parent" / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)

    with pytest.raises(StoreLockError):
        store.open(now_ms=1)

    assert not path.exists()


def test_nonregular_database_path_fails_closed(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    path.mkdir()
    store = TwinCommandStore(path, policy=control_policy)

    with pytest.raises(CorruptStoreError):
        store.open(now_ms=1)

    assert path.is_dir()
    assert Path(f"{path}.lock").exists()


def test_unsupported_schema_fails_closed_without_downgrade(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    _create_schema_meta_only(path, version=2)
    original = path.read_bytes()
    store = TwinCommandStore(path, policy=control_policy)

    with pytest.raises(UnsupportedSchemaError):
        store.open(now_ms=999)

    assert path.read_bytes() == original
    assert Path(f"{path}.lock").exists()
    with _connect(path) as connection:
        assert connection.execute(
            "SELECT schema_version, created_at_ms FROM schema_meta"
        ).fetchall() == [(2, 123)]


def test_nonempty_sqlite_without_schema_meta_fails_closed_without_mutation(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    with _connect(path) as connection:
        connection.execute("CREATE TABLE existing_data(value TEXT NOT NULL)")
        connection.execute("INSERT INTO existing_data(value) VALUES ('keep me')")
    original = path.read_bytes()
    store = TwinCommandStore(path, policy=control_policy)

    with pytest.raises(MigrationError):
        store.open(now_ms=1)

    assert path.read_bytes() == original
    assert _schema_objects(path, "table") == {"existing_data"}
    assert Path(f"{path}.lock").exists()


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [(1, 1), (2, 2)],
        [("one", 1)],
        [(0, 1)],
        [(1, -1)],
    ],
)
def test_invalid_schema_authority_rows_fail_closed_without_mutation(
    tmp_path: Path,
    control_policy: ControlPolicy,
    rows: list[tuple[object, object]],
) -> None:
    path = tmp_path / "twin.db"
    _create_unconstrained_schema_meta(path, rows)
    original = path.read_bytes()
    store = TwinCommandStore(path, policy=control_policy)

    with pytest.raises(MigrationError):
        store.open(now_ms=1)

    assert path.read_bytes() == original


def test_malformed_schema_meta_fails_closed_without_mutation(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    with _connect(path) as connection:
        connection.execute("CREATE TABLE schema_meta(schema_version INTEGER)")
        connection.execute("INSERT INTO schema_meta(schema_version) VALUES (1)")
    original = path.read_bytes()
    store = TwinCommandStore(path, policy=control_policy)

    with pytest.raises(CorruptStoreError):
        store.open(now_ms=1)

    assert path.read_bytes() == original


def test_schema_object_set_mismatch_fails_closed_without_recreation(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    store.close()
    with _connect(path) as connection:
        connection.execute("CREATE TABLE unexpected(value TEXT)")
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == (
            "delete",
        )
    original = path.read_bytes()

    with pytest.raises(MigrationError):
        TwinCommandStore(path, policy=control_policy).open(now_ms=2)

    assert path.read_bytes() == original
    assert _schema_objects(path, "table") == _EXPECTED_TABLES | {"unexpected"}


def test_schema_object_definition_mismatch_fails_closed_without_recreation(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    store.close()
    with _connect(path) as connection:
        connection.execute("DROP INDEX idx_commands_predecessor")
        connection.execute(
            "CREATE INDEX idx_commands_predecessor ON commands(command_id)"
        )
    original = path.read_bytes()

    with pytest.raises(MigrationError):
        TwinCommandStore(path, policy=control_policy).open(now_ms=2)

    assert path.read_bytes() == original


def test_unexpected_destructive_trigger_is_rejected_before_it_can_execute(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    store.close()
    with _connect(path) as connection:
        connection.execute(
            """
            CREATE TRIGGER destructive_devices
            AFTER INSERT ON devices
            BEGIN
                DELETE FROM devices WHERE device_id = NEW.device_id;
            END
            """
        )
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == (
            "delete",
        )
    original = path.read_bytes()
    reopened = TwinCommandStore(path, policy=control_policy)

    try:
        with pytest.raises(MigrationError):
            reopened.open(now_ms=2)
    finally:
        reopened.close()
    with pytest.raises(TwinStoreError):
        reopened.observe_device(
            device_id="device-1",
            observed_at_ms=10,
            observed_wire_id=20,
            observed_wire_id_set=30,
        )

    assert path.read_bytes() == original
    with _connect(path) as connection:
        assert connection.execute("SELECT * FROM devices").fetchall() == []
        assert connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'trigger'"
        ).fetchall() == [("destructive_devices",)]


def test_unexpected_view_is_rejected_without_database_mutation(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    store.close()
    with _connect(path) as connection:
        connection.execute(
            "CREATE VIEW device_view AS SELECT device_id FROM devices"
        )
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == (
            "delete",
        )
    original = path.read_bytes()
    reopened = TwinCommandStore(path, policy=control_policy)

    try:
        with pytest.raises(MigrationError):
            reopened.open(now_ms=2)
    finally:
        reopened.close()

    assert path.read_bytes() == original
    with _connect(path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'view'"
        ).fetchall() == [("device_view",)]


# pylint: disable-next=too-many-locals
def test_existing_database_path_swap_before_writable_open_is_rejected_unmodified(
    tmp_path: Path,
    control_policy: ControlPolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "twin.db"
    replacement = tmp_path / "replacement.db"
    saved_original = tmp_path / "saved-original.db"
    for db_path, created_at_ms in ((path, 1), (replacement, 2)):
        creator = TwinCommandStore(db_path, policy=control_policy)
        creator.open(now_ms=created_at_ms)
        creator.close()
        with _connect(db_path) as connection:
            assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == (
                "delete",
            )
    original_bytes = path.read_bytes()
    replacement_bytes = replacement.read_bytes()
    real_connect = sqlite3.connect
    connect_count = 0

    def swapping_connect(database: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal connect_count
        connect_count += 1
        if connect_count == 2:
            path.rename(saved_original)
            replacement.rename(path)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(store_module.sqlite3, "connect", swapping_connect)
    reopened = TwinCommandStore(path, policy=control_policy)

    try:
        with pytest.raises(CorruptStoreError):
            reopened.open(now_ms=3)
    finally:
        reopened.close()

    assert saved_original.read_bytes() == original_bytes
    assert path.read_bytes() == replacement_bytes


def test_existing_database_disappearance_before_writable_open_is_not_recreated(
    tmp_path: Path,
    control_policy: ControlPolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "twin.db"
    saved_original = tmp_path / "saved-original.db"
    creator = TwinCommandStore(path, policy=control_policy)
    creator.open(now_ms=1)
    creator.close()
    with _connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == (
            "delete",
        )
    original_bytes = path.read_bytes()
    real_connect = sqlite3.connect
    connect_count = 0

    def disappearing_connect(database: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal connect_count
        connect_count += 1
        if connect_count == 2:
            path.rename(saved_original)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(store_module.sqlite3, "connect", disappearing_connect)
    reopened = TwinCommandStore(path, policy=control_policy)

    with pytest.raises(CorruptStoreError):
        reopened.open(now_ms=2)

    assert not path.exists()
    assert saved_original.read_bytes() == original_bytes


def test_verify_health_rejects_database_path_inode_replacement(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    replacement = tmp_path / "replacement.db"
    saved_original = tmp_path / "saved-original.db"
    replacement_store = TwinCommandStore(replacement, policy=control_policy)
    replacement_store.open(now_ms=2)
    replacement_store.close()
    owner = TwinCommandStore(path, policy=control_policy)
    owner.open(now_ms=1)
    original_identity = path.stat().st_dev, path.stat().st_ino
    replacement_identity = replacement.stat().st_dev, replacement.stat().st_ino
    path.rename(saved_original)
    replacement.rename(path)

    with pytest.raises(CorruptStoreError):
        owner.verify_health()

    assert (saved_original.stat().st_dev, saved_original.stat().st_ino) == (
        original_identity
    )
    assert (path.stat().st_dev, path.stat().st_ino) == replacement_identity
    path.rename(replacement)
    saved_original.rename(path)
    owner.close()


def test_quick_check_non_ok_result_is_corruption() -> None:
    cursor = SimpleNamespace(fetchall=lambda: [("forced integrity failure",)])
    connection = SimpleNamespace(execute=lambda _sql: cursor)

    with pytest.raises(CorruptStoreError):
        TwinCommandStore._run_quick_check(connection)  # type: ignore[arg-type]  # pylint: disable=protected-access


def test_migration_statement_failure_rolls_back_and_keeps_database_and_lock(
    tmp_path: Path,
    control_policy: ControlPolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "twin.db"
    original_statements = store_module._SCHEMA_STATEMENTS  # pylint: disable=protected-access
    monkeypatch.setattr(
        store_module,
        "_SCHEMA_STATEMENTS",
        original_statements[:2]
        + ("CREATE TABLE broken syntax",)
        + original_statements[2:],
    )
    store = TwinCommandStore(path, policy=control_policy)

    with pytest.raises(MigrationError):
        store.open(now_ms=1)

    assert path.exists()
    assert path.stat().st_size > 0
    assert _schema_objects(path, "table") == set()
    lock_path = Path(f"{path}.lock")
    lock_inode = lock_path.stat().st_ino
    monkeypatch.setattr(store_module, "_SCHEMA_STATEMENTS", original_statements)
    database_after_failure = path.read_bytes()

    still_fail_closed = TwinCommandStore(path, policy=control_policy)
    with pytest.raises(MigrationError):
        still_fail_closed.open(now_ms=2)
    assert path.read_bytes() == database_after_failure
    assert lock_path.stat().st_ino == lock_inode
    assert _schema_objects(path, "table") == set()


def test_migration_begin_failure_is_reported_without_rollback_attempt() -> None:
    def fail_begin(_statement: str, _parameters: object = None) -> None:
        raise sqlite3.OperationalError("forced begin failure")

    connection = SimpleNamespace(in_transaction=False, execute=fail_begin)
    typed_connection = cast(sqlite3.Connection, connection)

    with pytest.raises(MigrationError):
        TwinCommandStore._create_schema(  # pylint: disable=protected-access
            typed_connection,
            now_ms=1,
        )


def test_observe_device_inserts_observed_plus_one_without_seed(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    with _connect(path) as connection:
        assert connection.execute("SELECT * FROM devices").fetchall() == []

    state = store.observe_device(
        device_id="device-1",
        observed_at_ms=100,
        observed_wire_id=40,
        observed_wire_id_set=90,
    )

    assert state == DeviceState(
        device_id="device-1",
        first_seen_at_ms=100,
        last_seen_at_ms=100,
        next_wire_id=41,
        next_wire_id_set=91,
    )
    store.close()


def test_observe_device_advances_counters_monotonically_and_preserves_first_seen(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)
    store.open(now_ms=1)
    store.observe_device(
        device_id="device-1",
        observed_at_ms=100,
        observed_wire_id=40,
        observed_wire_id_set=90,
    )

    older = store.observe_device(
        device_id="device-1",
        observed_at_ms=90,
        observed_wire_id=39,
        observed_wire_id_set=89,
    )
    newer = store.observe_device(
        device_id="device-1",
        observed_at_ms=200,
        observed_wire_id=50,
        observed_wire_id_set=95,
    )

    assert older == DeviceState("device-1", 100, 100, 41, 91)
    assert newer == DeviceState("device-1", 100, 200, 51, 96)
    store.close()


def test_observe_device_rolls_back_sqlite_failure(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    with _connect(path) as connection:
        connection.execute("DROP TABLE devices")

    with pytest.raises(TwinStoreError):
        store.observe_device(
            device_id="device-1",
            observed_at_ms=10,
            observed_wire_id=20,
            observed_wire_id_set=30,
        )

    store.close()


@pytest.mark.parametrize(
    ("column_name", "poisoned_value", "storage_class"),
    [
        ("first_seen_at_ms", 1.5, "real"),
        ("last_seen_at_ms", "poison", "text"),
        ("next_wire_id", 1.5, "real"),
        ("next_wire_id_set", "poison", "text"),
        ("next_wire_id", float(_MAX_SQLITE_INTEGER + 1), "real"),
    ],
)
def test_observe_device_rejects_poisoned_persisted_integers_and_degrades(
    tmp_path: Path,
    control_policy: ControlPolicy,
    column_name: str,
    poisoned_value: object,
    storage_class: str,
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    store.observe_device(
        device_id="device-1",
        observed_at_ms=100,
        observed_wire_id=40,
        observed_wire_id_set=90,
    )
    with _connect(path) as connection:
        connection.execute(
            f"UPDATE devices SET {column_name} = ? WHERE device_id = 'device-1'",
            (poisoned_value,),
        )
        assert connection.execute(
            f"SELECT typeof({column_name}) FROM devices WHERE device_id = 'device-1'"
        ).fetchone() == (storage_class,)

    with pytest.raises(TwinStoreError, match="persisted device") as error:
        store.observe_device(
            device_id="device-1",
            observed_at_ms=200,
            observed_wire_id=50,
            observed_wire_id_set=95,
        )

    assert len(str(error.value)) <= 1024
    assert store._connection is not None  # pylint: disable=protected-access
    assert not store._connection.in_transaction  # pylint: disable=protected-access
    with pytest.raises(TwinStoreError, match="persisted device"):
        store.verify_health()
    store.close()


def test_observe_device_rollback_failure_remains_degraded_and_unhealthy(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    store.observe_device(
        device_id="device-1",
        observed_at_ms=100,
        observed_wire_id=40,
        observed_wire_id_set=90,
    )
    with _connect(path) as connection:
        connection.execute(
            "UPDATE devices SET next_wire_id = 'poison' WHERE device_id = 'device-1'"
        )
    assert store._connection is not None  # pylint: disable=protected-access
    rollback_connection = _RollbackFaultConnection(
        store._connection  # pylint: disable=protected-access
    )
    store._connection = cast(  # pylint: disable=protected-access
        sqlite3.Connection, rollback_connection
    )

    with pytest.raises(TwinStoreError, match="rollback failed") as error:
        store.observe_device(
            device_id="device-1",
            observed_at_ms=200,
            observed_wire_id=50,
            observed_wire_id_set=95,
        )

    assert len(str(error.value)) <= 1024
    assert rollback_connection.rollback_attempts == 1
    with pytest.raises(TwinStoreError, match="rollback failed"):
        store.verify_health()
    store.close()


def test_observe_device_keyboard_interrupt_rolls_back_and_remains_healthy(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)
    store.open(now_ms=1)
    assert store._connection is not None  # pylint: disable=protected-access
    cancellation = KeyboardInterrupt("cancelled observation")
    interrupted_connection = _ObservationBaseExceptionConnection(
        store._connection,  # pylint: disable=protected-access
        operation_error=cancellation,
    )
    store._connection = cast(  # pylint: disable=protected-access
        sqlite3.Connection, interrupted_connection
    )

    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            store.observe_device(
                device_id="device-1",
                observed_at_ms=10,
                observed_wire_id=20,
                observed_wire_id_set=30,
            )

        assert caught.value is cancellation
        assert interrupted_connection.rollback_attempts == 1
        assert not interrupted_connection.in_transaction
        assert store.verify_health() == PragmaSnapshot("wal", 2, 1, 5000)
        assert store.observe_device(
            device_id="device-1",
            observed_at_ms=10,
            observed_wire_id=20,
            observed_wire_id_set=30,
        ) == DeviceState("device-1", 10, 10, 21, 31)
    finally:
        store.close()


def test_observe_device_base_exception_rollback_failure_degrades_store(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)
    store.open(now_ms=1)
    assert store._connection is not None  # pylint: disable=protected-access
    failed_connection = _ObservationBaseExceptionConnection(
        store._connection,  # pylint: disable=protected-access
        operation_error=BaseException("cancelled observation"),
        rollback_error=BaseException("rollback halted"),
    )
    store._connection = cast(  # pylint: disable=protected-access
        sqlite3.Connection, failed_connection
    )

    try:
        with pytest.raises(TwinStoreError, match="rollback failed") as caught:
            store.observe_device(
                device_id="device-1",
                observed_at_ms=10,
                observed_wire_id=20,
                observed_wire_id_set=30,
            )

        assert "cancelled observation" in str(caught.value)
        assert "rollback halted" in str(caught.value)
        assert len(str(caught.value)) <= 1024
        assert failed_connection.rollback_attempts == 1
        assert failed_connection.in_transaction
        with pytest.raises(TwinStoreError, match="rollback failed"):
            store.verify_health()
    finally:
        store.close()


def test_observe_device_reports_begin_failure_without_rollback_attempt(
    tmp_path: Path,
    control_policy: ControlPolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)
    store.open(now_ms=1)
    real_connection = store._connection  # pylint: disable=protected-access

    def fail_begin(_statement: str, _parameters: object = None) -> None:
        raise sqlite3.OperationalError("forced begin failure")

    failed_connection = SimpleNamespace(
        in_transaction=False,
        execute=fail_begin,
        close=lambda: None,
    )
    store._connection = cast(Any, failed_connection)  # pylint: disable=protected-access
    monkeypatch.setattr(
        TwinCommandStore,
        "verify_health",
        lambda _self: PragmaSnapshot("wal", 2, 1, 5000),
    )

    with pytest.raises(TwinStoreError):
        store.observe_device(
            device_id="device-1",
            observed_at_ms=10,
            observed_wire_id=20,
            observed_wire_id_set=30,
        )

    store._connection = real_connection  # pylint: disable=protected-access
    store.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("device_id", ""),
        ("device_id", " device-1"),
        ("device_id", "device-1 "),
        ("device_id", "x" * 129),
        ("device_id", 1),
        ("observed_at_ms", -1),
        ("observed_at_ms", True),
        ("observed_wire_id", -1),
        ("observed_wire_id", True),
        ("observed_wire_id_set", -1),
    ],
)
def test_observe_device_rejects_invalid_identity_observations(
    tmp_path: Path,
    control_policy: ControlPolicy,
    field: str,
    value: object,
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)
    store.open(now_ms=1)
    values: dict[str, object] = {
        "device_id": "device-1",
        "observed_at_ms": 10,
        "observed_wire_id": 20,
        "observed_wire_id_set": 30,
    }
    values[field] = value

    with pytest.raises(ValueError):
        store.observe_device(**values)  # type: ignore[arg-type]

    store.close()


def test_observe_device_rejects_timestamp_sqlite_overflow(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)
    store.open(now_ms=1)

    with pytest.raises(OverflowError):
        store.observe_device(
            device_id="device-1",
            observed_at_ms=_MAX_SQLITE_INTEGER + 1,
            observed_wire_id=20,
            observed_wire_id_set=30,
        )

    assert store.verify_health().journal_mode == "wal"
    store.close()


@pytest.mark.parametrize("counter_name", ["observed_wire_id", "observed_wire_id_set"])
@pytest.mark.parametrize(
    "overflow_value", [_MAX_SQLITE_INTEGER, _MAX_SQLITE_INTEGER + 1]
)
def test_observe_device_sqlite_counter_overflow_disables_control_without_wrap(
    tmp_path: Path,
    control_policy: ControlPolicy,
    counter_name: str,
    overflow_value: int,
) -> None:
    path = tmp_path / "twin.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    values = {
        "device_id": "device-1",
        "observed_at_ms": 10,
        "observed_wire_id": 20,
        "observed_wire_id_set": 30,
    }
    values[counter_name] = overflow_value

    with pytest.raises(OverflowError):
        store.observe_device(**values)  # type: ignore[arg-type]
    with pytest.raises(TwinStoreError):
        store.verify_health()

    store.close()
    with _connect(path) as connection:
        assert connection.execute("SELECT * FROM devices").fetchall() == []


def test_state_count_order_matches_every_command_state() -> None:
    assert tuple(CommandState) == (
        CommandState.PENDING,
        CommandState.RETRY_PENDING,
        CommandState.AWAITING_ACK,
        CommandState.AWAITING_EVENT,
        CommandState.CONFIRMED,
        CommandState.INCOMPLETE,
        CommandState.FAILED,
        CommandState.EXPIRED,
        CommandState.SUPERSEDED,
    )


def _observe_for_lifecycle(
    store: TwinCommandStore,
    *,
    device_id: str = "123",
    observed_at_ms: int = 90,
) -> None:
    store.observe_device(
        device_id=device_id,
        observed_at_ms=observed_at_ms,
        observed_wire_id=14_000_000,
        observed_wire_id_set=1_786_000_000,
    )


# pylint: disable-next=too-many-arguments
def _enqueue_lifecycle(
    store: TwinCommandStore,
    *,
    value_text: str,
    received_at_ms: int,
    device_id: str = "123",
    table_name: str = "tbl_box_prms",
    item_name: str = "MODE",
    ingress_id: str | None = None,
):
    try:
        store.read_device(device_id)
    except StoreRecordNotFound:
        _observe_for_lifecycle(store, device_id=device_id)
    ingress = ControlIngress(
        ingress_id or f"ing-{device_id}-{received_at_ms}-{value_text}",
        received_at_ms,
        f"oig/{device_id}/control/set",
        device_id,
        False,
        f'{{"value":{value_text}}}',
    )
    return store.enqueue_command(
        ingress,
        device_id=device_id,
        table_name=table_name,
        item_name=item_name,
        value_text=value_text,
    ).command


def test_enqueue_commits_ingress_command_and_transition_atomically(
    store: TwinCommandStore,
) -> None:
    _observe_for_lifecycle(store, observed_at_ms=100)
    ingress = ControlIngress(
        "ing-1",
        110,
        "oig/123/control/set",
        "123",
        False,
        '{"value":2}',
    )

    result = store.enqueue_command(
        ingress,
        device_id="123",
        table_name="tbl_box_prms",
        item_name="MODE",
        value_text="2",
    )

    assert result.command.state is CommandState.PENDING
    assert result.command.pending_expires_at_ms == 110 + store.policy.pending_ttl_ms
    assert store.read_ingress("ing-1").command_id == result.command.command_id
    transition = store.read_transitions(result.command.command_id)[0]
    assert (
        transition.from_state,
        transition.to_state,
        transition.reason,
    ) == (None, CommandState.PENDING, "accepted_ingress")
    assert result.snapshots == (
        result.snapshots[0],
    )
    assert result.snapshots[0].command == store.read_command(result.command.command_id)
    assert result.snapshots[0].transition == transition


def test_enqueue_after_attempt_creates_successor_without_mutating_predecessor(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    first = _enqueue_lifecycle(store, value_text="1", received_at_ms=100)
    claimed = store.prepare_next_attempt(
        device_id="123",
        session_id="session-a",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )

    second = _enqueue_lifecycle(store, value_text="2", received_at_ms=300)

    assert claimed.disposition is ClaimDisposition.PREPARED
    assert store.read_command(first.command_id) == claimed.command
    assert second.predecessor_command_id == first.command_id
    assert second.state is CommandState.PENDING


def test_enqueue_replaces_only_unsent_successor(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    first = _enqueue_lifecycle(store, value_text="1", received_at_ms=100)
    store.prepare_next_attempt(
        device_id="123",
        session_id="session-a",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )
    second = _enqueue_lifecycle(store, value_text="2", received_at_ms=300)

    third_result = store.enqueue_command(
        ControlIngress(
            "ing-third",
            400,
            "oig/123/control/set",
            "123",
            False,
            '{"value":3}',
        ),
        device_id="123",
        table_name="tbl_box_prms",
        item_name="MODE",
        value_text="3",
    )

    assert store.read_command(first.command_id).state is CommandState.AWAITING_ACK
    assert store.read_command(second.command_id).state is CommandState.SUPERSEDED
    assert third_result.command.predecessor_command_id == first.command_id
    assert tuple(
        snapshot.transition.reason for snapshot in third_result.snapshots
    ) == ("replaced_unsent", "accepted_ingress")


def test_every_transition_and_attempt_reuses_original_command_and_audit_ids(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    original = _enqueue_lifecycle(store, value_text="2", received_at_ms=100)
    prepared = store.prepare_next_attempt(
        device_id="123",
        session_id="session-a",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )
    store.mark_write_started(
        command_id=original.command_id,
        attempt_number=1,
        session_id="session-a",
        started_at_ms=201,
    )
    store.mark_attempt_drained(
        command_id=original.command_id,
        attempt_number=1,
        session_id="session-a",
        drained_at_ms=202,
    )

    assert prepared.attempt is not None
    assert prepared.attempt.command_id == original.command_id
    assert {
        (row.command_id, row.audit_id)
        for row in store.read_transitions(original.command_id)
    } == {(original.command_id, original.audit_id)}


def test_prepare_uses_fifo_tie_breaking_and_keeps_devices_independent(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    first = _enqueue_lifecycle(
        store,
        value_text="1",
        received_at_ms=100,
        item_name="MODE",
        ingress_id="ing-mode",
    )
    second = _enqueue_lifecycle(
        store,
        value_text="1",
        received_at_ms=100,
        item_name="SA",
        ingress_id="ing-sa",
    )
    other = _enqueue_lifecycle(
        store,
        value_text="1",
        received_at_ms=100,
        device_id="456",
        ingress_id="ing-other",
    )

    first_claim = store.prepare_next_attempt(
        device_id="123",
        session_id="device-123",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )
    other_claim = store.prepare_next_attempt(
        device_id="456",
        session_id="device-456",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )

    assert first_claim.command is not None
    assert first_claim.command.command_id == min(first.command_id, second.command_id)
    assert other_claim.command is not None
    assert other_claim.command.command_id == other.command_id


def test_predecessor_and_identical_awaiting_event_commands_block_successor(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    first = _enqueue_lifecycle(store, value_text="2", received_at_ms=100)
    claimed = store.prepare_next_attempt(
        device_id="123",
        session_id="session-a",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )
    same_target = _enqueue_lifecycle(store, value_text="2", received_at_ms=300)

    blocked = store.prepare_next_attempt(
        device_id="123",
        session_id="session-b",
        prepared_at_ms=301,
        render=deterministic_renderer,
    )

    assert claimed.command is not None
    assert claimed.command.command_id == first.command_id
    assert same_target.predecessor_command_id == first.command_id
    assert blocked.disposition is ClaimDisposition.ACTIVE_DELIVERY_ELSEWHERE

    store.mark_write_started(
        command_id=first.command_id,
        attempt_number=1,
        session_id="session-a",
        started_at_ms=302,
    )
    store.mark_attempt_drained(
        command_id=first.command_id,
        attempt_number=1,
        session_id="session-a",
        drained_at_ms=303,
    )
    acknowledged = store.acknowledge_and_prepare_next(
        command_id=first.command_id,
        attempt_number=1,
        session_id="session-a",
        response=SettingResponse("ACK", "Setting", None, "a" * 64),
        received_at_ms=304,
        evidence_frame=b"ack",
        render=deterministic_renderer,
    )

    assert acknowledged.next_claim.disposition is ClaimDisposition.NO_ELIGIBLE
    assert store.prepare_next_attempt(
        device_id="123",
        session_id="session-b",
        prepared_at_ms=305,
        render=deterministic_renderer,
    ).disposition is ClaimDisposition.NO_ELIGIBLE


class _RecordingRenderer:  # pylint: disable=too-few-public-methods
    """Attempt renderer that exposes literal context fields to tests."""

    def __init__(self) -> None:
        self.contexts: list[AttemptRenderContext] = []

    def __call__(self, context: AttemptRenderContext) -> RenderedAttempt:
        self.contexts.append(context)
        call_number = len(self.contexts)
        return RenderedAttempt(
            tsec_text=f"tsec-{call_number}",
            ver_text=f"{call_number:05d}",
            crc_text=f"{call_number + 100:05d}",
            wire_frame=f"wire-attempt-{call_number}".encode("ascii"),
        )


def test_prepare_commits_frame_attempt_and_deadline_atomically(
    store: TwinCommandStore,
) -> None:
    pending = _enqueue_lifecycle(store, value_text="2", received_at_ms=100)
    renderer = _RecordingRenderer()

    result = store.prepare_next_attempt(
        device_id="123",
        session_id="session-a",
        prepared_at_ms=1_786_003_920_000,
        render=renderer,
    )

    assert result.disposition is ClaimDisposition.PREPARED
    assert result.command is not None
    assert result.command.command_id == pending.command_id
    assert result.command.state is CommandState.AWAITING_ACK
    assert result.command.attempt_count == 1
    assert result.command.ack_deadline_ms == 1_786_003_950_000
    assert result.command.wire_dt == "2026-08-06 10:12:00"
    assert result.attempt is not None
    assert result.command.last_wire_frame == result.attempt.wire_frame
    assert result.attempt.wire_length == len(b"wire-attempt-1")
    assert result.attempt.write_outcome is AttemptWriteOutcome.PREPARED
    assert tuple(snapshot.transition.reason for snapshot in result.snapshots) == (
        "selected",
        "attempt_prepared",
    )


def test_retry_preserves_stable_fields_and_refreshes_attempt_fields_only(
    store: TwinCommandStore,
) -> None:
    _enqueue_lifecycle(store, value_text="2", received_at_ms=100)
    renderer = _RecordingRenderer()
    first = store.prepare_next_attempt(
        device_id="123",
        session_id="a",
        prepared_at_ms=200,
        render=renderer,
    )
    assert first.command is not None and first.attempt is not None
    store.mark_write_started(
        command_id=first.command.command_id,
        attempt_number=1,
        session_id="a",
        started_at_ms=201,
    )
    store.mark_write_unknown(
        command_id=first.command.command_id,
        attempt_number=1,
        session_id="a",
        occurred_at_ms=202,
        error="drain reset",
    )

    second = store.prepare_next_attempt(
        device_id="123",
        session_id="b",
        prepared_at_ms=300,
        render=renderer,
    )

    assert second.command is not None and second.attempt is not None
    assert (
        first.command.wire_id,
        first.command.wire_id_set,
        first.command.wire_dt,
        first.command.device_id,
        first.command.table_name,
        first.command.item_name,
        first.command.value_text,
    ) == (
        second.command.wire_id,
        second.command.wire_id_set,
        second.command.wire_dt,
        second.command.device_id,
        second.command.table_name,
        second.command.item_name,
        second.command.value_text,
    )
    assert first.attempt.tsec_text != second.attempt.tsec_text
    assert first.attempt.ver_text != second.attempt.ver_text
    assert first.attempt.crc_text != second.attempt.crc_text
    assert renderer.contexts[1].used_ver_texts == ("00001",)


def test_prepare_render_failure_is_terminal_without_consuming_counters_or_attempt(
    store: TwinCommandStore,
) -> None:
    command = _enqueue_lifecycle(store, value_text="2", received_at_ms=100)
    before = store.read_device("123")

    result = store.prepare_next_attempt(
        device_id="123",
        session_id="a",
        prepared_at_ms=200,
        render=lambda _context: RenderedAttempt(
            tsec_text="200",
            ver_text="00001",
            crc_text="00001",
            wire_frame=b"",
        ),
    )

    assert result.disposition is ClaimDisposition.RENDER_FAILED
    assert result.command is not None
    assert result.command.state is CommandState.FAILED
    assert store.read_device("123") == before
    with pytest.raises(StoreRecordNotFound):
        store.read_attempt(command.command_id, 1)


def test_repeated_claim_same_session_is_noop_and_other_session_is_rejected(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    _enqueue_lifecycle(store, value_text="2", received_at_ms=100)
    prepared = store.prepare_next_attempt(
        device_id="123",
        session_id="same",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )
    transitions_before = store.read_transitions()

    same = store.prepare_next_attempt(
        device_id="123",
        session_id="same",
        prepared_at_ms=201,
        render=deterministic_renderer,
    )
    other = store.prepare_next_attempt(
        device_id="123",
        session_id="other",
        prepared_at_ms=201,
        render=deterministic_renderer,
    )

    assert same == type(prepared)(ClaimDisposition.NO_ELIGIBLE, None, None)
    assert other == type(prepared)(
        ClaimDisposition.ACTIVE_DELIVERY_ELSEWHERE, None, None
    )
    assert store.read_transitions() == transitions_before


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("command_id", "wrong-command"),
        ("attempt_number", 2),
        ("session_id", "wrong-session"),
    ],
)
def test_write_mutations_reject_nonexact_command_attempt_session_cas(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
    changed_field: str,
    changed_value: object,
) -> None:
    command = _enqueue_lifecycle(store, value_text="2", received_at_ms=100)
    store.prepare_next_attempt(
        device_id="123",
        session_id="a",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )
    values: dict[str, object] = {
        "command_id": command.command_id,
        "attempt_number": 1,
        "session_id": "a",
        "started_at_ms": 201,
    }
    values[changed_field] = changed_value

    with pytest.raises(StaleAttemptError):
        store.mark_write_started(**values)  # type: ignore[arg-type]

    assert store.read_attempt(command.command_id, 1).write_outcome is (
        AttemptWriteOutcome.PREPARED
    )


def test_write_outcomes_distinguish_failed_unknown_and_drained(
    store_factory: Callable[[int], TwinCommandStore],
    deterministic_renderer: AttemptRenderer,
) -> None:
    failed_store = store_factory(8)
    failed_command = _enqueue_lifecycle(
        failed_store, value_text="1", received_at_ms=100
    )
    failed_store.prepare_next_attempt(
        device_id="123",
        session_id="failed",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )
    failed = failed_store.mark_write_failed(
        command_id=failed_command.command_id,
        attempt_number=1,
        session_id="failed",
        occurred_at_ms=201,
        error="write refused",
    )

    unknown_store = store_factory(8)
    unknown_command = _enqueue_lifecycle(
        unknown_store, value_text="1", received_at_ms=100
    )
    unknown_store.prepare_next_attempt(
        device_id="123",
        session_id="unknown",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )
    unknown_store.mark_write_started(
        command_id=unknown_command.command_id,
        attempt_number=1,
        session_id="unknown",
        started_at_ms=201,
    )
    unknown = unknown_store.mark_write_unknown(
        command_id=unknown_command.command_id,
        attempt_number=1,
        session_id="unknown",
        occurred_at_ms=202,
        error="drain reset",
    )

    drained_store = store_factory(8)
    drained_command = _enqueue_lifecycle(
        drained_store, value_text="1", received_at_ms=100
    )
    drained_store.prepare_next_attempt(
        device_id="123",
        session_id="drained",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )
    drained_store.mark_write_started(
        command_id=drained_command.command_id,
        attempt_number=1,
        session_id="drained",
        started_at_ms=201,
    )
    drained = drained_store.mark_attempt_drained(
        command_id=drained_command.command_id,
        attempt_number=1,
        session_id="drained",
        drained_at_ms=202,
    )

    assert failed.command.state is CommandState.RETRY_PENDING
    assert failed.attempt is not None
    assert failed.attempt.write_outcome is AttemptWriteOutcome.FAILED
    assert unknown.command.state is CommandState.RETRY_PENDING
    assert unknown.attempt is not None
    assert unknown.attempt.write_outcome is AttemptWriteOutcome.UNKNOWN
    assert drained.command.state is CommandState.AWAITING_ACK
    assert drained.attempt is not None
    assert drained.attempt.write_outcome is AttemptWriteOutcome.DRAINED
    assert drained.command.completed_at_ms is None


def test_attempt_limits_one_and_eight_are_terminal(
    store_factory: Callable[[int], TwinCommandStore],
    deterministic_renderer: AttemptRenderer,
) -> None:
    one = store_factory(1)
    one_command = _enqueue_lifecycle(one, value_text="1", received_at_ms=1)
    one.prepare_next_attempt(
        device_id="123",
        session_id="a",
        prepared_at_ms=10,
        render=deterministic_renderer,
    )
    failed = one.release_for_retry(
        command_id=one_command.command_id,
        attempt_number=1,
        session_id="a",
        occurred_at_ms=11,
        reason=RetryReason.ACK_TIMEOUT,
    )
    assert failed.command.state is CommandState.FAILED

    eight = store_factory(8)
    eight_command = _enqueue_lifecycle(eight, value_text="1", received_at_ms=1)
    for attempt_number in range(1, 9):
        session = f"session-{attempt_number}"
        prepared = eight.prepare_next_attempt(
            device_id="123",
            session_id=session,
            prepared_at_ms=attempt_number * 10,
            render=deterministic_renderer,
        )
        assert prepared.command is not None
        assert prepared.command.attempt_count == attempt_number
        released = eight.release_for_retry(
            command_id=eight_command.command_id,
            attempt_number=attempt_number,
            session_id=session,
            occurred_at_ms=attempt_number * 10 + 1,
            reason=RetryReason.DISCONNECT,
        )
    assert released.command.state is CommandState.FAILED
    assert eight.prepare_next_attempt(
        device_id="123",
        session_id="session-9",
        prepared_at_ms=90,
        render=deterministic_renderer,
    ).disposition is ClaimDisposition.NO_ELIGIBLE


def test_attempt_transition_failure_rolls_back_write_outcome(
    tmp_path: Path,
    control_policy: ControlPolicy,
    deterministic_renderer: AttemptRenderer,
) -> None:
    path = tmp_path / "atomic-attempt.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    command = _enqueue_lifecycle(store, value_text="2", received_at_ms=100)
    store.prepare_next_attempt(
        device_id="123",
        session_id="a",
        prepared_at_ms=200,
        render=deterministic_renderer,
    )
    assert store._connection is not None  # pylint: disable=protected-access
    fault = _TransitionInsertFaultConnection(
        store._connection  # pylint: disable=protected-access
    )
    store._connection = cast(  # pylint: disable=protected-access
        sqlite3.Connection, fault
    )

    with pytest.raises(TwinStoreError, match="transition insert"):
        store.mark_write_started(
            command_id=command.command_id,
            attempt_number=1,
            session_id="a",
            started_at_ms=201,
        )

    store.close()
    reopened = TwinCommandStore(path, policy=control_policy)
    reopened.open(now_ms=300)
    try:
        assert reopened.read_attempt(command.command_id, 1).write_outcome is (
            AttemptWriteOutcome.PREPARED
        )
        assert tuple(
            transition.reason
            for transition in reopened.read_transitions(command.command_id)
        ) == ("accepted_ingress", "selected", "attempt_prepared")
    finally:
        reopened.close()


def _prepared_and_drained(
    store: TwinCommandStore,
    renderer: AttemptRenderer,
    *,
    session: str = "a",
    now_ms: int = 200,
    value_text: str = "2",
):
    command = _enqueue_lifecycle(
        store,
        value_text=value_text,
        received_at_ms=max(1, now_ms - 100),
    )
    prepared = store.prepare_next_attempt(
        device_id="123",
        session_id=session,
        prepared_at_ms=now_ms,
        render=renderer,
    )
    assert prepared.attempt is not None
    store.mark_write_started(
        command_id=command.command_id,
        attempt_number=prepared.attempt.attempt_number,
        session_id=session,
        started_at_ms=now_ms + 1,
    )
    return store.mark_attempt_drained(
        command_id=command.command_id,
        attempt_number=prepared.attempt.attempt_number,
        session_id=session,
        drained_at_ms=now_ms + 2,
    ).attempt


def _response(
    result: Literal["ACK", "NACK"] = "ACK",
    *,
    fingerprint: str = "a" * 64,
    rdt_text: str | None = "06.08.2026 10:12:00",
    reason: str | None = "Setting",
) -> SettingResponse:
    return SettingResponse(
        result=result,
        reason=reason,
        rdt_text=rdt_text,
        fingerprint=fingerprint,
    )


def test_acknowledge_moves_to_awaiting_event_with_inclusive_deadline(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    attempt = _prepared_and_drained(store, deterministic_renderer)
    assert attempt is not None

    result = store.acknowledge_and_prepare_next(
        command_id=attempt.command_id,
        attempt_number=attempt.attempt_number,
        session_id="a",
        received_at_ms=attempt.ack_deadline_ms,
        response=_response(),
        evidence_frame=b"ack",
        render=deterministic_renderer,
    )

    assert result.accepted_command is not None
    assert result.accepted_command.state is CommandState.AWAITING_EVENT
    assert result.accepted_command.event_deadline_ms == (
        attempt.ack_deadline_ms + store.policy.event_timeout_ms
    )
    assert result.accepted_command.completed_at_ms is None
    assert result.next_claim.disposition is ClaimDisposition.NO_ELIGIBLE
    assert not result.duplicate


def test_ack_rejects_wrong_session_and_late_response_without_mutation(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    attempt = _prepared_and_drained(store, deterministic_renderer)
    assert attempt is not None
    command_before = store.read_command(attempt.command_id)

    with pytest.raises(StaleAttemptError):
        store.acknowledge_and_prepare_next(
            command_id=attempt.command_id,
            attempt_number=1,
            session_id="wrong",
            received_at_ms=attempt.ack_deadline_ms,
            response=_response(),
            evidence_frame=b"ack",
            render=deterministic_renderer,
        )
    with pytest.raises(StaleAttemptError):
        store.acknowledge_and_prepare_next(
            command_id=attempt.command_id,
            attempt_number=1,
            session_id="a",
            received_at_ms=attempt.ack_deadline_ms + 1,
            response=_response(),
            evidence_frame=b"ack",
            render=deterministic_renderer,
        )

    assert store.read_command(attempt.command_id) == command_before


def test_ack_deduplicates_session_batch_and_rejects_decreasing_parseable_rdt(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    first_attempt = _prepared_and_drained(store, deterministic_renderer)
    assert first_attempt is not None
    store.acknowledge_and_prepare_next(
        command_id=first_attempt.command_id,
        attempt_number=1,
        session_id="a",
        received_at_ms=210,
        response=_response(fingerprint="b" * 64),
        evidence_frame=b"ack-1",
        render=deterministic_renderer,
    )
    duplicate = store.acknowledge_and_prepare_next(
        command_id=first_attempt.command_id,
        attempt_number=1,
        session_id="a",
        received_at_ms=211,
        response=_response(fingerprint="b" * 64),
        evidence_frame=b"ack-1",
        render=deterministic_renderer,
    )
    assert duplicate.duplicate
    assert duplicate.accepted_command is None
    assert duplicate.snapshots == ()

    # A distinct target can be active in the same UUID session batch while the
    # first command awaits event evidence.
    next_command = _enqueue_lifecycle(
        store,
        value_text="1",
        received_at_ms=220,
        item_name="SA",
    )
    store.prepare_next_attempt(
        device_id="123",
        session_id="a",
        prepared_at_ms=230,
        render=deterministic_renderer,
    )
    with pytest.raises(StaleAttemptError, match="Rdt"):
        store.acknowledge_and_prepare_next(
            command_id=next_command.command_id,
            attempt_number=1,
            session_id="a",
            received_at_ms=231,
            response=_response(
                fingerprint="c" * 64,
                rdt_text="06.08.2026 10:11:59",
            ),
            evidence_frame=b"ack-2",
            render=deterministic_renderer,
        )


def test_ack_atomically_prepares_next_eligible_distinct_target(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    first_attempt = _prepared_and_drained(store, deterministic_renderer)
    assert first_attempt is not None
    second = _enqueue_lifecycle(
        store,
        value_text="1",
        received_at_ms=250,
        item_name="SA",
    )

    result = store.acknowledge_and_prepare_next(
        command_id=first_attempt.command_id,
        attempt_number=1,
        session_id="a",
        received_at_ms=300,
        response=_response(),
        evidence_frame=b"ack",
        render=deterministic_renderer,
    )

    assert result.next_claim.disposition is ClaimDisposition.PREPARED
    assert result.next_claim.command is not None
    assert result.next_claim.command.command_id == second.command_id
    assert tuple(snapshot.transition.reason for snapshot in result.snapshots) == (
        "ack_received",
        "selected",
        "attempt_prepared",
    )


def test_nack_is_terminal_with_diagnostic_and_exact_duplicate_is_noop(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    attempt = _prepared_and_drained(store, deterministic_renderer)
    assert attempt is not None
    nack = _response(
        "NACK",
        fingerprint="d" * 64,
        reason="WC",
    )

    result = store.mark_nack(
        command_id=attempt.command_id,
        attempt_number=1,
        session_id="a",
        response=nack,
        received_at_ms=attempt.ack_deadline_ms,
        evidence_frame=b"nack",
    )
    duplicate = store.mark_nack(
        command_id=attempt.command_id,
        attempt_number=1,
        session_id="a",
        response=nack,
        received_at_ms=attempt.ack_deadline_ms,
        evidence_frame=b"nack",
    )

    assert result.accepted_command is not None
    assert result.accepted_command.state is CommandState.FAILED
    assert result.accepted_command.last_error == "WC"
    assert result.accepted_command.completed_at_ms == attempt.ack_deadline_ms
    assert duplicate.duplicate
    assert duplicate.accepted_command is None
    assert duplicate.snapshots == ()
    with pytest.raises(StaleAttemptError):
        store.release_for_retry(
            command_id=attempt.command_id,
            attempt_number=1,
            session_id="a",
            occurred_at_ms=attempt.ack_deadline_ms + 1,
            reason=RetryReason.DISCONNECT,
        )


def test_deadline_sweep_is_strict_and_respects_event_timeout_switch(
    store_factory: Callable[[int], TwinCommandStore],
    deterministic_renderer: AttemptRenderer,
) -> None:
    pending_store = store_factory(8)
    pending = _enqueue_lifecycle(pending_store, value_text="2", received_at_ms=100)
    at_pending = pending_store.sweep_deadlines(
        now_ms=pending.pending_expires_at_ms
    )
    assert at_pending.expired_pending == 0
    after_pending = pending_store.sweep_deadlines(
        now_ms=pending.pending_expires_at_ms + 1
    )
    assert after_pending.expired_pending == 1
    assert pending_store.read_command(pending.command_id).state is CommandState.EXPIRED

    ack_store = store_factory(8)
    attempt = _prepared_and_drained(ack_store, deterministic_renderer)
    assert attempt is not None
    assert ack_store.sweep_deadlines(
        now_ms=attempt.ack_deadline_ms
    ).retry_pending == 0
    assert ack_store.sweep_deadlines(
        now_ms=attempt.ack_deadline_ms + 1
    ).retry_pending == 1

    event_store = store_factory(8)
    event_attempt = _prepared_and_drained(event_store, deterministic_renderer)
    assert event_attempt is not None
    ack = event_store.acknowledge_and_prepare_next(
        command_id=event_attempt.command_id,
        attempt_number=1,
        session_id="a",
        response=_response(),
        received_at_ms=300,
        evidence_frame=b"ack",
        render=deterministic_renderer,
    )
    assert ack.accepted_command is not None
    event_deadline = ack.accepted_command.event_deadline_ms
    assert event_deadline is not None
    assert event_store.sweep_deadlines(
        now_ms=event_deadline,
        include_event_timeouts=True,
    ).incomplete_event_timeout == 0
    assert event_store.sweep_deadlines(
        now_ms=event_deadline + 1,
        include_event_timeouts=False,
    ).incomplete_event_timeout == 0
    assert event_store.read_command(event_attempt.command_id).state is (
        CommandState.AWAITING_EVENT
    )
    assert event_store.sweep_deadlines(
        now_ms=event_deadline + 1,
        include_event_timeouts=True,
    ).incomplete_event_timeout == 1


def test_ack_timeout_at_attempt_limit_is_terminal(
    store_factory: Callable[[int], TwinCommandStore],
    deterministic_renderer: AttemptRenderer,
) -> None:
    store = store_factory(1)
    attempt = _prepared_and_drained(store, deterministic_renderer)
    assert attempt is not None

    report = store.sweep_deadlines(now_ms=attempt.ack_deadline_ms + 1)

    assert report.failed_attempt_limit == 1
    assert report.retry_pending == 0
    assert store.read_command(attempt.command_id).state is CommandState.FAILED


def test_different_value_successor_can_run_while_predecessor_awaits_event(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    attempt = _prepared_and_drained(store, deterministic_renderer)
    assert attempt is not None
    acknowledged = store.acknowledge_and_prepare_next(
        command_id=attempt.command_id,
        attempt_number=1,
        session_id="a",
        response=_response(),
        received_at_ms=300,
        evidence_frame=b"ack",
        render=deterministic_renderer,
    )
    assert acknowledged.accepted_command is not None
    successor = _enqueue_lifecycle(store, value_text="3", received_at_ms=400)

    claimed = store.prepare_next_attempt(
        device_id="123",
        session_id="b",
        prepared_at_ms=500,
        render=deterministic_renderer,
    )

    assert claimed.disposition is ClaimDisposition.PREPARED
    assert claimed.command is not None
    assert claimed.command.command_id == successor.command_id


def test_event_timeout_candidates_and_exact_cas_are_strict(
    store: TwinCommandStore,
    deterministic_renderer: AttemptRenderer,
) -> None:
    attempt = _prepared_and_drained(store, deterministic_renderer)
    assert attempt is not None
    ack = store.acknowledge_and_prepare_next(
        command_id=attempt.command_id,
        attempt_number=1,
        session_id="a",
        response=_response(),
        received_at_ms=300,
        evidence_frame=b"ack",
        render=deterministic_renderer,
    )
    assert ack.accepted_command is not None
    deadline = ack.accepted_command.event_deadline_ms
    assert deadline is not None

    assert store.read_event_timeout_candidates(now_ms=deadline) == ()
    candidates = store.read_event_timeout_candidates(now_ms=deadline + 1)
    assert tuple(candidate.command_id for candidate in candidates) == (
        attempt.command_id,
    )
    assert store.mark_event_incomplete(
        command_id=attempt.command_id,
        expected_event_deadline_ms=deadline + 1,
        now_ms=deadline + 2,
    ) is None
    assert store.mark_event_incomplete(
        command_id=attempt.command_id,
        expected_event_deadline_ms=deadline,
        now_ms=deadline,
    ) is None
    completed = store.mark_event_incomplete(
        command_id=attempt.command_id,
        expected_event_deadline_ms=deadline,
        now_ms=deadline + 1,
    )
    assert completed is not None
    assert completed.command.state is CommandState.INCOMPLETE


def test_recover_maps_active_states_and_preserves_terminals(
    tmp_path: Path,
    deterministic_renderer: AttemptRenderer,
) -> None:
    policy = ControlPolicy(
        ack_timeout_ms=10,
        event_timeout_ms=20,
        pending_ttl_ms=30,
        max_attempts=1,
    )
    path = tmp_path / "recovery.db"
    store = TwinCommandStore(path, policy=policy)
    store.open(now_ms=1)
    pending = _enqueue_lifecycle(store, value_text="1", received_at_ms=10)
    awaiting = _enqueue_lifecycle(
        store,
        value_text="1",
        received_at_ms=11,
        item_name="SA",
    )
    store.prepare_next_attempt(
        device_id="123",
        session_id="active",
        prepared_at_ms=20,
        render=deterministic_renderer,
    )
    assert store.read_command(awaiting.command_id).state in {
        CommandState.PENDING,
        CommandState.AWAITING_ACK,
    }
    store.close()

    reopened = TwinCommandStore(path, policy=policy)
    reopened.open(now_ms=100)
    report = reopened.recover(now_ms=100)

    assert report.expired_pending == 1
    assert report.failed_attempt_limit == 1
    assert reopened.read_command(pending.command_id).state in {
        CommandState.EXPIRED,
        CommandState.FAILED,
    }
    assert reopened.read_command(awaiting.command_id).state in {
        CommandState.EXPIRED,
        CommandState.FAILED,
    }
    transitions_before = reopened.read_transitions()
    second = reopened.recover(now_ms=200)
    assert second == type(report)(0, 0, 0, 0, 0)
    assert reopened.read_transitions() == transitions_before
    reopened.close()


def test_ingress_dispositions_status_and_single_nonterminal_use_public_reads(
    store: TwinCommandStore,
) -> None:
    rejected = store.record_ingress_disposition(
        ControlIngress("reject-1", 10, "bad", None, False, "{}"),
        disposition=IngressDisposition.REJECTED_TOPIC,
        reason="wrong topic",
    )
    proxy = store.record_proxy_control_ingress(
        ControlIngress("proxy-1", 11, "oig/proxy/control", None, False, "{}"),
        reason="proxy mode",
    )
    command = _enqueue_lifecycle(store, value_text="2", received_at_ms=100)

    assert rejected == store.read_ingress("reject-1")
    assert proxy.disposition is IngressDisposition.ACCEPTED_PROXY_CONTROL
    assert store.read_latest_ingress().command_id == command.command_id
    assert store.single_nonterminal("123") == command
    status = store.status_snapshot("123")
    assert status.count(CommandState.PENDING) == 1
    assert status.nonterminal_commands == 1
    assert status.control_available


def test_read_methods_raise_record_not_found_for_absent_records(
    store: TwinCommandStore,
) -> None:
    with pytest.raises(StoreRecordNotFound):
        store.read_device("missing")
    with pytest.raises(StoreRecordNotFound):
        store.read_command("missing")
    with pytest.raises(StoreRecordNotFound):
        store.read_attempt("missing", 1)
    with pytest.raises(StoreRecordNotFound):
        store.read_ingress("missing")
    with pytest.raises(StoreRecordNotFound):
        store.read_latest_ingress()
    with pytest.raises(StoreRecordNotFound):
        store.read_event_receipt("a" * 64)
    with pytest.raises(StoreRecordNotFound):
        store.read_transitions("missing")
    with pytest.raises(StoreRecordNotFound):
        store.single_nonterminal("missing")


@pytest.mark.parametrize(
    "rendered",
    [
        RenderedAttempt("1", "00001", "00001", bytearray(b"frame")),
        RenderedAttempt("1", "1", "00001", b"frame"),
        RenderedAttempt("1", "00001", "1", b"frame"),
        RenderedAttempt("1", "65536", "00001", b"frame"),
    ],
)
def test_invalid_render_contract_fails_terminal_without_attempt(
    store: TwinCommandStore,
    rendered: RenderedAttempt,
) -> None:
    command = _enqueue_lifecycle(store, value_text="2", received_at_ms=100)
    result = store.prepare_next_attempt(
        device_id="123",
        session_id="a",
        prepared_at_ms=200,
        render=lambda _context: rendered,
    )

    assert result.disposition is ClaimDisposition.RENDER_FAILED
    assert result.command is not None
    assert result.command.state is CommandState.FAILED
    with pytest.raises(StoreRecordNotFound):
        store.read_attempt(command.command_id, 1)


def test_duplicate_attempt_version_is_rejected_without_consuming_retry(
    store: TwinCommandStore,
) -> None:
    command = _enqueue_lifecycle(store, value_text="2", received_at_ms=100)

    def repeated_version(_context: AttemptRenderContext) -> RenderedAttempt:
        return RenderedAttempt("1", "00001", "00001", b"frame")

    store.prepare_next_attempt(
        device_id="123",
        session_id="a",
        prepared_at_ms=200,
        render=repeated_version,
    )
    store.release_for_retry(
        command_id=command.command_id,
        attempt_number=1,
        session_id="a",
        occurred_at_ms=201,
        reason=RetryReason.DISCONNECT,
    )

    failed = store.prepare_next_attempt(
        device_id="123",
        session_id="b",
        prepared_at_ms=300,
        render=repeated_version,
    )

    assert failed.disposition is ClaimDisposition.RENDER_FAILED
    assert failed.command is not None
    assert failed.command.attempt_count == 1
    with pytest.raises(StoreRecordNotFound):
        store.read_attempt(command.command_id, 2)


def test_enqueue_keyboard_interrupt_rolls_back_and_preserves_control_flow(
    tmp_path: Path,
    control_policy: ControlPolicy,
) -> None:
    path = tmp_path / "enqueue-cancel.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    _observe_for_lifecycle(store)
    assert store._connection is not None  # pylint: disable=protected-access
    cancellation = KeyboardInterrupt("cancelled enqueue")
    fault = _LifecycleFaultConnection(
        store._connection,  # pylint: disable=protected-access
        operation_error=cancellation,
    )
    store._connection = cast(  # pylint: disable=protected-access
        sqlite3.Connection, fault
    )

    with pytest.raises(KeyboardInterrupt) as caught:
        store.enqueue_command(
            ControlIngress("cancelled", 100, "topic", "123", False, "{}"),
            device_id="123",
            table_name="tbl_box_prms",
            item_name="MODE",
            value_text="2",
        )

    assert caught.value is cancellation
    assert fault.rollback_attempts == 1
    assert store.verify_health() == PragmaSnapshot("wal", 2, 1, 5000)
    with pytest.raises(StoreRecordNotFound):
        store.read_ingress("cancelled")
    assert store.status_snapshot().nonterminal_commands == 0
    store.close()


def test_lifecycle_rollback_failure_degrades_fail_closed(
    tmp_path: Path,
    control_policy: ControlPolicy,
) -> None:
    store = TwinCommandStore(tmp_path / "rollback-failure.db", policy=control_policy)
    store.open(now_ms=1)
    _observe_for_lifecycle(store)
    assert store._connection is not None  # pylint: disable=protected-access
    fault = _LifecycleFaultConnection(
        store._connection,  # pylint: disable=protected-access
        operation_error=BaseException("cancelled enqueue"),
        rollback_error=BaseException("rollback halted"),
    )
    store._connection = cast(  # pylint: disable=protected-access
        sqlite3.Connection, fault
    )

    with pytest.raises(TwinStoreError, match="rollback failed"):
        store.enqueue_command(
            ControlIngress("cancelled", 100, "topic", "123", False, "{}"),
            device_id="123",
            table_name="tbl_box_prms",
            item_name="MODE",
            value_text="2",
        )

    assert fault.rollback_attempts == 1
    with pytest.raises(TwinStoreError, match="rollback failed"):
        store.verify_health()
    store.close()


def test_post_commit_reporting_failure_degrades_with_durable_commit_preserved(
    tmp_path: Path,
    control_policy: ControlPolicy,
) -> None:
    path = tmp_path / "ambiguous-commit.db"
    store = TwinCommandStore(path, policy=control_policy)
    store.open(now_ms=1)
    _observe_for_lifecycle(store)
    assert store._connection is not None  # pylint: disable=protected-access
    fault = _CommitOutcomeFaultConnection(
        store._connection  # pylint: disable=protected-access
    )
    store._connection = cast(  # pylint: disable=protected-access
        sqlite3.Connection, fault
    )

    with pytest.raises(TwinStoreError, match="ambiguous"):
        store.enqueue_command(
            ControlIngress("committed", 100, "topic", "123", False, "{}"),
            device_id="123",
            table_name="tbl_box_prms",
            item_name="MODE",
            value_text="2",
        )
    with pytest.raises(TwinStoreError, match="ambiguous"):
        store.verify_health()
    store.close()

    reopened = TwinCommandStore(path, policy=control_policy)
    reopened.open(now_ms=200)
    try:
        assert reopened.read_ingress("committed").command_id is not None
        assert reopened.status_snapshot().count(CommandState.PENDING) == 1
    finally:
        reopened.close()


def test_read_command_rejects_noninteger_persisted_timestamp_and_degrades(
    tmp_path: Path,
    control_policy: ControlPolicy,
) -> None:
    path = tmp_path / "poisoned-command.db"
    creator = TwinCommandStore(path, policy=control_policy)
    creator.open(now_ms=1)
    command = _enqueue_lifecycle(creator, value_text="2", received_at_ms=100)
    creator.close()
    with _connect(path) as connection:
        connection.execute(
            "UPDATE commands SET updated_at_ms = 100.5 WHERE command_id = ?",
            (command.command_id,),
        )
        assert connection.execute(
            "SELECT typeof(updated_at_ms) FROM commands WHERE command_id = ?",
            (command.command_id,),
        ).fetchone() == ("real",)

    reopened = TwinCommandStore(path, policy=control_policy)
    reopened.open(now_ms=200)
    with pytest.raises(TwinStoreError, match="persisted command"):
        reopened.read_command(command.command_id)
    with pytest.raises(TwinStoreError, match="persisted command"):
        reopened.verify_health()
    reopened.close()


# pylint: disable-next=too-many-locals
def test_recover_handles_retry_kept_and_overdue_event_states(
    tmp_path: Path,
    deterministic_renderer: AttemptRenderer,
) -> None:
    policy = ControlPolicy(
        ack_timeout_ms=10,
        event_timeout_ms=20,
        pending_ttl_ms=1_000,
        max_attempts=2,
    )

    retry_path = tmp_path / "recover-retry.db"
    retry_store = TwinCommandStore(retry_path, policy=policy)
    retry_store.open(now_ms=1)
    retry_command = _enqueue_lifecycle(
        retry_store, value_text="2", received_at_ms=10
    )
    retry_store.prepare_next_attempt(
        device_id="123",
        session_id="a",
        prepared_at_ms=20,
        render=deterministic_renderer,
    )
    retry_store.close()
    retry_store = TwinCommandStore(retry_path, policy=policy)
    retry_store.open(now_ms=30)
    retry_report = retry_store.recover(now_ms=30)
    assert retry_report.retry_pending == 1
    assert retry_store.read_command(retry_command.command_id).state is (
        CommandState.RETRY_PENDING
    )
    retry_store.close()

    event_path = tmp_path / "recover-event.db"
    event_store = TwinCommandStore(event_path, policy=policy)
    event_store.open(now_ms=1)
    event_attempt = _prepared_and_drained(
        event_store,
        deterministic_renderer,
        now_ms=20,
    )
    assert event_attempt is not None
    ack = event_store.acknowledge_and_prepare_next(
        command_id=event_attempt.command_id,
        attempt_number=1,
        session_id="a",
        response=_response(),
        received_at_ms=25,
        evidence_frame=b"ack",
        render=deterministic_renderer,
    )
    assert ack.accepted_command is not None
    deadline = ack.accepted_command.event_deadline_ms
    assert deadline == 45
    event_store.close()

    event_store = TwinCommandStore(event_path, policy=policy)
    event_store.open(now_ms=45)
    kept = event_store.recover(now_ms=45)
    assert kept.kept_awaiting_event == 1
    assert event_store.read_command(event_attempt.command_id).state is (
        CommandState.AWAITING_EVENT
    )
    event_store.close()

    event_store = TwinCommandStore(event_path, policy=policy)
    event_store.open(now_ms=46)
    incomplete = event_store.recover(now_ms=46)
    assert incomplete.incomplete_event_timeout == 1
    assert event_store.read_command(event_attempt.command_id).state is (
        CommandState.INCOMPLETE
    )
    transitions = event_store.read_transitions(event_attempt.command_id)
    event_store.close()

    terminal_store = TwinCommandStore(event_path, policy=policy)
    terminal_store.open(now_ms=100)
    assert terminal_store.recover(now_ms=100) == type(incomplete)(0, 0, 0, 0, 0)
    assert terminal_store.read_transitions(event_attempt.command_id) == transitions
    terminal_store.close()
