"""Behavioral and SQLite artifact tests for the durable twin store."""

# pyright: reportMissingImports=false
# pylint: disable=import-error,missing-function-docstring,too-many-lines

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any, cast, Iterator

import pytest

from twin.state import CommandState, ControlPolicy, DeviceState, PragmaSnapshot
from twin.store import (
    CorruptStoreError,
    MigrationError,
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
