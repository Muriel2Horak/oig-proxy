"""Behavioral and SQLite artifact tests for the durable twin store."""

# pyright: reportMissingImports=false
# pylint: disable=import-error,missing-function-docstring

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any, cast

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


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _schema_objects(path: Path, object_type: str) -> set[str]:
    with _connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
            (object_type,),
        ).fetchall()
    return {str(row[0]) for row in rows}


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
