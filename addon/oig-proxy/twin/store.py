"""Fail-closed SQLite source of truth for local-setting transactions."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import sqlite3
import stat
import threading

from .state import ControlPolicy, DeviceState, PragmaSnapshot


_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MS = 5000
_MAX_SQLITE_INTEGER = (1 << 63) - 1
_EXPECTED_PRAGMAS = PragmaSnapshot(
    journal_mode="wal",
    synchronous=2,
    foreign_keys=1,
    busy_timeout_ms=_BUSY_TIMEOUT_MS,
)

_CREATE_SCHEMA_META = """
CREATE TABLE schema_meta (
    schema_version INTEGER PRIMARY KEY CHECK (schema_version >= 1),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
)
"""

_CREATE_DEVICES = """
CREATE TABLE devices (
    device_id TEXT PRIMARY KEY CHECK (length(device_id) BETWEEN 1 AND 128),
    first_seen_at_ms INTEGER NOT NULL CHECK (first_seen_at_ms >= 0),
    last_seen_at_ms INTEGER NOT NULL CHECK (last_seen_at_ms >= first_seen_at_ms),
    next_wire_id INTEGER NOT NULL CHECK (next_wire_id >= 0),
    next_wire_id_set INTEGER NOT NULL CHECK (next_wire_id_set >= 0)
)
"""

_CREATE_COMMANDS = """
CREATE TABLE commands (
    command_id TEXT PRIMARY KEY,
    audit_id TEXT NOT NULL UNIQUE,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    table_name TEXT NOT NULL CHECK (length(table_name) BETWEEN 1 AND 128),
    item_name TEXT NOT NULL CHECK (length(item_name) BETWEEN 1 AND 128),
    value_text TEXT NOT NULL CHECK (length(value_text) BETWEEN 1 AND 1024),
    raw_ingress_text TEXT NOT NULL CHECK (length(raw_ingress_text) <= 16384),
    state TEXT NOT NULL CHECK (state IN (
        'pending','retry_pending','awaiting_ack','awaiting_event',
        'confirmed','incomplete','failed','expired','superseded'
    )),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= created_at_ms),
    pending_expires_at_ms INTEGER NOT NULL CHECK (pending_expires_at_ms >= created_at_ms),
    wire_id INTEGER,
    wire_id_set INTEGER,
    wire_dt TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 8),
    active_session_id TEXT,
    ack_deadline_ms INTEGER,
    event_deadline_ms INTEGER,
    acked_at_ms INTEGER,
    ack_device_rdt TEXT,
    completed_at_ms INTEGER,
    predecessor_command_id TEXT REFERENCES commands(command_id),
    last_wire_frame BLOB CHECK (last_wire_frame IS NULL OR length(last_wire_frame) <= 1048576),
    last_error TEXT CHECK (last_error IS NULL OR length(last_error) <= 1024),
    UNIQUE (command_id, audit_id),
    CHECK (predecessor_command_id IS NULL OR predecessor_command_id <> command_id),
    CHECK ((wire_id IS NULL AND wire_id_set IS NULL AND wire_dt IS NULL)
        OR (wire_id IS NOT NULL AND wire_id_set IS NOT NULL AND wire_dt IS NOT NULL))
)
"""

_CREATE_CONTROL_INGRESS_AUDIT = """
CREATE TABLE control_ingress_audit (
    ingress_id TEXT PRIMARY KEY,
    received_at_ms INTEGER NOT NULL CHECK (received_at_ms >= 0),
    topic TEXT NOT NULL CHECK (length(topic) <= 1024),
    topic_device_id TEXT CHECK (topic_device_id IS NULL OR length(topic_device_id) <= 128),
    retain INTEGER NOT NULL CHECK (retain IN (0, 1)),
    disposition TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(reason) <= 1024),
    raw_text TEXT NOT NULL CHECK (length(raw_text) <= 16384),
    command_id TEXT,
    audit_id TEXT,
    CHECK ((command_id IS NULL AND audit_id IS NULL)
        OR (command_id IS NOT NULL AND audit_id IS NOT NULL)),
    FOREIGN KEY (command_id, audit_id) REFERENCES commands(command_id, audit_id)
)
"""

_CREATE_COMMAND_ATTEMPTS = """
CREATE TABLE command_attempts (
    command_id TEXT NOT NULL REFERENCES commands(command_id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number BETWEEN 1 AND 8),
    session_id TEXT NOT NULL CHECK (length(session_id) BETWEEN 1 AND 128),
    prepared_at_ms INTEGER NOT NULL CHECK (prepared_at_ms >= 0),
    write_started_at_ms INTEGER,
    drain_completed_at_ms INTEGER,
    ack_deadline_ms INTEGER NOT NULL CHECK (ack_deadline_ms >= prepared_at_ms),
    tsec_text TEXT NOT NULL,
    ver_text TEXT NOT NULL CHECK (length(ver_text) = 5),
    crc_text TEXT NOT NULL CHECK (length(crc_text) = 5),
    wire_frame BLOB NOT NULL CHECK (length(wire_frame) <= 1048576),
    wire_length INTEGER NOT NULL CHECK (wire_length = length(wire_frame)),
    write_outcome TEXT NOT NULL CHECK (
        write_outcome IN ('prepared','started','drained','unknown','failed')
    ),
    write_error TEXT CHECK (write_error IS NULL OR length(write_error) <= 1024),
    response_fingerprint TEXT CHECK (
        response_fingerprint IS NULL OR length(response_fingerprint) = 64
    ),
    response_rdt TEXT,
    PRIMARY KEY (command_id, attempt_number)
)
"""

_CREATE_COMMAND_TRANSITIONS = """
CREATE TABLE command_transitions (
    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL,
    audit_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
    attempt_number INTEGER,
    session_id TEXT,
    reason TEXT NOT NULL CHECK (length(reason) <= 1024),
    error_text TEXT CHECK (error_text IS NULL OR length(error_text) <= 1024),
    wire_frame BLOB CHECK (wire_frame IS NULL OR length(wire_frame) <= 1048576),
    evidence_frame BLOB CHECK (evidence_frame IS NULL OR length(evidence_frame) <= 1048576),
    FOREIGN KEY (command_id, audit_id) REFERENCES commands(command_id, audit_id)
)
"""

_CREATE_EVENT_RECEIPTS = """
CREATE TABLE event_receipts (
    evidence_id TEXT PRIMARY KEY CHECK (length(evidence_id) = 64),
    received_at_ms INTEGER NOT NULL CHECK (received_at_ms >= 0),
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    event_id_set INTEGER NOT NULL CHECK (event_id_set >= 0),
    device_dt TEXT NOT NULL,
    table_name TEXT NOT NULL CHECK (length(table_name) BETWEEN 1 AND 128),
    item_name TEXT NOT NULL CHECK (length(item_name) BETWEEN 1 AND 128),
    old_value_text TEXT NOT NULL CHECK (length(old_value_text) <= 1024),
    new_value_text TEXT NOT NULL CHECK (length(new_value_text) <= 1024),
    evidence_frame BLOB NOT NULL CHECK (length(evidence_frame) <= 1048576),
    disposition TEXT NOT NULL CHECK (disposition IN ('confirmed','unmatched')),
    command_id TEXT REFERENCES commands(command_id),
    duplicate_count INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
    last_seen_at_ms INTEGER NOT NULL CHECK (last_seen_at_ms >= received_at_ms)
)
"""

_CREATE_INDEX_COMMANDS_FIFO = """
CREATE INDEX idx_commands_fifo
ON commands(device_id, state, created_at_ms, command_id)
"""

_CREATE_INDEX_COMMANDS_EVENT_MATCH = """
CREATE INDEX idx_commands_event_match
ON commands(device_id, table_name, item_name, value_text, state, acked_at_ms)
"""

_CREATE_INDEX_COMMANDS_PREDECESSOR = """
CREATE INDEX idx_commands_predecessor ON commands(predecessor_command_id)
"""

_CREATE_INDEX_ONE_AWAITING_ACK = """
CREATE UNIQUE INDEX ux_commands_one_awaiting_ack_per_device
ON commands(device_id) WHERE state = 'awaiting_ack'
"""

_CREATE_INDEX_ONE_UNSENT_SUCCESSOR = """
CREATE UNIQUE INDEX ux_commands_one_unsent_successor_per_target
ON commands(device_id, table_name, item_name)
WHERE state = 'pending' AND attempt_count = 0
"""

_CREATE_INDEX_ONE_CONFIRMATION = """
CREATE UNIQUE INDEX ux_event_receipts_one_confirmation_per_command
ON event_receipts(command_id) WHERE command_id IS NOT NULL
"""

_SCHEMA_STATEMENTS = (
    _CREATE_SCHEMA_META,
    _CREATE_DEVICES,
    _CREATE_COMMANDS,
    _CREATE_CONTROL_INGRESS_AUDIT,
    _CREATE_COMMAND_ATTEMPTS,
    _CREATE_COMMAND_TRANSITIONS,
    _CREATE_EVENT_RECEIPTS,
    _CREATE_INDEX_COMMANDS_FIFO,
    _CREATE_INDEX_COMMANDS_EVENT_MATCH,
    _CREATE_INDEX_COMMANDS_PREDECESSOR,
    _CREATE_INDEX_ONE_AWAITING_ACK,
    _CREATE_INDEX_ONE_UNSENT_SUCCESSOR,
    _CREATE_INDEX_ONE_CONFIRMATION,
)

_EXPECTED_SCHEMA_SQL = {
    ("table", "schema_meta"): _CREATE_SCHEMA_META,
    ("table", "devices"): _CREATE_DEVICES,
    ("table", "commands"): _CREATE_COMMANDS,
    ("table", "control_ingress_audit"): _CREATE_CONTROL_INGRESS_AUDIT,
    ("table", "command_attempts"): _CREATE_COMMAND_ATTEMPTS,
    ("table", "command_transitions"): _CREATE_COMMAND_TRANSITIONS,
    ("table", "event_receipts"): _CREATE_EVENT_RECEIPTS,
    ("index", "idx_commands_fifo"): _CREATE_INDEX_COMMANDS_FIFO,
    ("index", "idx_commands_event_match"): _CREATE_INDEX_COMMANDS_EVENT_MATCH,
    ("index", "idx_commands_predecessor"): _CREATE_INDEX_COMMANDS_PREDECESSOR,
    ("index", "ux_commands_one_awaiting_ack_per_device"): _CREATE_INDEX_ONE_AWAITING_ACK,
    ("index", "ux_commands_one_unsent_successor_per_target"): _CREATE_INDEX_ONE_UNSENT_SUCCESSOR,
    ("index", "ux_event_receipts_one_confirmation_per_command"): _CREATE_INDEX_ONE_CONFIRMATION,
}


class TwinStoreError(RuntimeError):
    """Base error for fail-closed durable store operations."""


class StoreLockError(TwinStoreError):
    """The exclusive process lock is unavailable or no longer trustworthy."""


class MigrationError(TwinStoreError):
    """The durable schema cannot be safely created or recognized."""


class UnsupportedSchemaError(MigrationError):
    """The database schema is newer than this runtime."""


class CorruptStoreError(TwinStoreError):
    """SQLite integrity validation failed."""


class StaleAttemptError(TwinStoreError):
    """An attempted mutation no longer matches the active delivery."""


class StoreRecordNotFound(LookupError):
    """A requested durable record does not exist."""


class TwinCommandStore:
    """Single-process owner of the durable local-setting database."""

    def __init__(self, db_path: str | os.PathLike[str], *, policy: ControlPolicy) -> None:
        if not isinstance(policy, ControlPolicy):
            raise TypeError("policy must be a ControlPolicy")
        self._db_path = Path(db_path)
        self._lock_path = Path(f"{self._db_path}.lock")
        self._policy = policy
        self._mutex = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._process_lock: tuple[int, tuple[int, int]] | None = None
        self._store_state: tuple[
            int, int, str | None, tuple[int, int] | None
        ] = (0, 0, None, None)

    @property
    def schema_version(self) -> int:
        """Return the opened schema version, or zero before opening."""
        with self._mutex:
            return self._store_state[0]

    @property
    def schema_created_at_ms(self) -> int:
        """Return the durable schema creation timestamp."""
        with self._mutex:
            return self._store_state[1]

    @property
    def policy(self) -> ControlPolicy:
        """Return immutable transaction lifecycle policy."""
        return self._policy

    @property
    def is_open(self) -> bool:
        """Return whether both SQLite and the process lock are owned."""
        with self._mutex:
            return self._connection is not None and self._process_lock is not None

    def open(self, *, now_ms: int) -> None:
        """Acquire the process lock and open or create exact schema v1."""
        _validate_sqlite_integer("now_ms", now_ms)
        with self._mutex:
            if self._connection is not None or self._process_lock is not None:
                raise TwinStoreError("store is already open")
            self._store_state = (0, 0, None, None)
            self._acquire_process_lock()
            try:
                file_state = self._database_file_state()
                bootstrap = file_state is None or file_state[1] == 0
                if file_state is not None:
                    database_identity = file_state[0]
                    if not bootstrap:
                        self._preflight_existing_database(database_identity)
                    connection = self._connect_database(mode="rw")
                    self._connection = connection
                    self._verify_database_identity(database_identity)
                    if not bootstrap:
                        self._validate_open_database(connection)
                else:
                    connection = sqlite3.connect(
                        self._db_path,
                        isolation_level=None,
                        check_same_thread=False,
                    )
                    self._connection = connection
                    created_state = self._database_file_state()
                    if created_state is None:
                        raise CorruptStoreError(
                            "SQLite did not create the requested database file"
                        )
                    database_identity = created_state[0]
                self._verify_database_identity(database_identity)
                self._configure_pragmas(connection)
                if bootstrap:
                    self._create_schema(connection, now_ms=now_ms)
                version, created_at_ms = self._read_schema_meta(connection)
                self._validate_schema_sql(connection)
                self._store_state = (
                    version,
                    created_at_ms,
                    None,
                    database_identity,
                )
                self.verify_health()
            except (TwinStoreError, OSError, sqlite3.Error) as error:
                try:
                    self._release_resources()
                except TwinStoreError as cleanup_error:
                    combined_reason = _bounded_message(
                        f"store open failed: {error}; cleanup failed: {cleanup_error}"
                    )
                    self._set_degradation(combined_reason)
                    raise TwinStoreError(combined_reason) from cleanup_error
                if isinstance(error, TwinStoreError):
                    raise
                if isinstance(error, sqlite3.DatabaseError):
                    raise CorruptStoreError(
                        f"failed to open SQLite store: {error}"
                    ) from error
                raise TwinStoreError(f"failed to open store: {error}") from error

    def close(self) -> None:
        """Close SQLite before releasing the process lock; retain all artifacts."""
        with self._mutex:
            self._release_resources()

    def pragma_snapshot(self) -> PragmaSnapshot:
        """Read required pragmas from the live SQLite connection."""
        with self._mutex:
            connection = self._require_connection()
            return self._read_pragmas(connection)

    def verify_health(self) -> PragmaSnapshot:
        """Verify lock identity, SQLite integrity, and required pragma readback."""
        with self._mutex:
            degradation_reason = self._store_state[2]
            if degradation_reason is not None:
                raise TwinStoreError(degradation_reason)
            connection = self._require_connection()
            self._verify_process_lock()
            database_identity = self._store_state[3]
            if database_identity is None:
                raise CorruptStoreError("database file identity is unavailable")
            self._verify_database_identity(database_identity)
            self._run_quick_check(connection)
            snapshot = self._read_pragmas(connection)
            if snapshot != _EXPECTED_PRAGMAS:
                raise TwinStoreError(
                    f"required SQLite pragmas changed: {snapshot!r}"
                )
            return snapshot

    def observe_device(
        self,
        *,
        device_id: str,
        observed_at_ms: int,
        observed_wire_id: int,
        observed_wire_id_set: int,
    ) -> DeviceState:
        """Insert/update exact identity and advance both counters past observations."""
        normalized_device_id = _validate_device_id(device_id)
        _validate_sqlite_integer("observed_at_ms", observed_at_ms)
        _validate_observed_counter("observed_wire_id", observed_wire_id)
        _validate_observed_counter("observed_wire_id_set", observed_wire_id_set)

        with self._mutex:
            self.verify_health()
            if (
                observed_wire_id >= _MAX_SQLITE_INTEGER
                or observed_wire_id_set >= _MAX_SQLITE_INTEGER
            ):
                degradation_reason = (
                    "observed device counter cannot advance within SQLite integer range"
                )
                self._set_degradation(degradation_reason)
                raise OverflowError(degradation_reason)

            return self._observe_device_locked(
                normalized_device_id=normalized_device_id,
                observed_at_ms=observed_at_ms,
                observed_wire_id=observed_wire_id,
                observed_wire_id_set=observed_wire_id_set,
            )

    def _observe_device_locked(
        self,
        *,
        normalized_device_id: str,
        observed_at_ms: int,
        observed_wire_id: int,
        observed_wire_id_set: int,
    ) -> DeviceState:
        """Persist one validated observation while the store mutex is held."""
        connection = self._require_connection()
        next_wire_id = observed_wire_id + 1
        next_wire_id_set = observed_wire_id_set + 1
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT first_seen_at_ms, last_seen_at_ms,
                       next_wire_id, next_wire_id_set
                FROM devices WHERE device_id = ?
                """,
                (normalized_device_id,),
            ).fetchone()
            if row is None:
                state = DeviceState(
                    device_id=normalized_device_id,
                    first_seen_at_ms=observed_at_ms,
                    last_seen_at_ms=observed_at_ms,
                    next_wire_id=next_wire_id,
                    next_wire_id_set=next_wire_id_set,
                )
                connection.execute(
                    """
                    INSERT INTO devices(
                        device_id, first_seen_at_ms, last_seen_at_ms,
                        next_wire_id, next_wire_id_set
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        state.device_id,
                        state.first_seen_at_ms,
                        state.last_seen_at_ms,
                        state.next_wire_id,
                        state.next_wire_id_set,
                    ),
                )
            else:
                state = _merge_persisted_device_state(
                    normalized_device_id=normalized_device_id,
                    row=row,
                    observed_at_ms=observed_at_ms,
                    next_wire_id=next_wire_id,
                    next_wire_id_set=next_wire_id_set,
                )
                connection.execute(
                    """
                    UPDATE devices
                    SET last_seen_at_ms = ?, next_wire_id = ?, next_wire_id_set = ?
                    WHERE device_id = ?
                    """,
                    (
                        state.last_seen_at_ms,
                        state.next_wire_id,
                        state.next_wire_id_set,
                        state.device_id,
                    ),
                )
            connection.execute("COMMIT")
            return state
        except Exception as error:  # pylint: disable=broad-exception-caught
            rollback_error: Exception | None = None
            try:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
            except Exception as caught:  # pylint: disable=broad-exception-caught
                rollback_error = caught
            if isinstance(error, TwinStoreError):
                reason = str(error)
            else:
                reason = f"failed to observe device: {error}"
            if rollback_error is not None:
                reason = f"{reason}; rollback failed: {rollback_error}"
            bounded_reason = _bounded_message(reason)
            self._set_degradation(bounded_reason)
            raise TwinStoreError(bounded_reason) from (rollback_error or error)

    def _acquire_process_lock(self) -> None:
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as error:
            raise StoreLockError(
                f"cannot open process lock {self._lock_path}: {error}"
            ) from error
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            descriptor_stat = os.fstat(fd)
            path_stat = os.stat(self._lock_path)
            descriptor_identity = (descriptor_stat.st_dev, descriptor_stat.st_ino)
            path_identity = (path_stat.st_dev, path_stat.st_ino)
            if descriptor_identity != path_identity:
                raise StoreLockError("process lock path changed during acquisition")
        except (BlockingIOError, OSError) as error:
            os.close(fd)
            raise StoreLockError(
                f"process lock is already held for {self._db_path}"
            ) from error
        except StoreLockError:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            raise
        self._process_lock = (fd, descriptor_identity)

    def _verify_process_lock(self) -> None:
        if self._process_lock is None:
            raise StoreLockError("process lock is not held")
        fd, expected_identity = self._process_lock
        try:
            descriptor_stat = os.fstat(fd)
            path_stat = os.stat(self._lock_path)
        except OSError as error:
            raise StoreLockError(f"process lock is not live: {error}") from error
        descriptor_identity = (descriptor_stat.st_dev, descriptor_stat.st_ino)
        path_identity = (path_stat.st_dev, path_stat.st_ino)
        if descriptor_identity != expected_identity:
            raise StoreLockError("process lock descriptor identity changed")
        if path_identity != expected_identity:
            raise StoreLockError("process lock path inode was replaced")

    def _preflight_existing_database(
        self, expected_identity: tuple[int, int]
    ) -> None:
        self._verify_database_identity(expected_identity)
        try:
            connection = self._connect_database(mode="ro")
        except (OSError, sqlite3.Error) as error:
            raise CorruptStoreError(
                f"cannot read existing SQLite store: {error}"
            ) from error
        self._connection = connection
        try:
            self._verify_database_identity(expected_identity)
            self._run_quick_check(connection)
            self._read_schema_meta(connection)
            self._validate_schema_sql(connection)
            self._verify_database_identity(expected_identity)
        except TwinStoreError:
            raise
        except sqlite3.Error as error:
            raise CorruptStoreError(
                f"cannot validate existing SQLite store: {error}"
            ) from error
        finally:
            self._close_connection()

    def _connect_database(self, *, mode: str) -> sqlite3.Connection:
        uri = f"{self._db_path.absolute().as_uri()}?mode={mode}"
        return sqlite3.connect(
            uri,
            uri=True,
            isolation_level=None,
            check_same_thread=False,
        )

    def _database_file_state(self) -> tuple[tuple[int, int], int] | None:
        try:
            path_stat = self._db_path.stat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise CorruptStoreError(
                f"cannot inspect database path: {error}"
            ) from error
        if not stat.S_ISREG(path_stat.st_mode):
            raise CorruptStoreError("database path is not a regular file")
        return (path_stat.st_dev, path_stat.st_ino), path_stat.st_size

    def _verify_database_identity(
        self, expected_identity: tuple[int, int]
    ) -> None:
        file_state = self._database_file_state()
        if file_state is None:
            raise CorruptStoreError("database path disappeared")
        if file_state[0] != expected_identity:
            raise CorruptStoreError("database path inode was replaced")

    def _validate_open_database(self, connection: sqlite3.Connection) -> None:
        self._run_quick_check(connection)
        self._read_schema_meta(connection)
        self._validate_schema_sql(connection)

    def _set_degradation(self, reason: str) -> None:
        self._store_state = (
            self._store_state[0],
            self._store_state[1],
            _bounded_message(reason),
            self._store_state[3],
        )

    @staticmethod
    def _configure_pragmas(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")

    @staticmethod
    def _create_schema(connection: sqlite3.Connection, *, now_ms: int) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_meta(schema_version, created_at_ms) VALUES (?, ?)",
                (_SCHEMA_VERSION, now_ms),
            )
            connection.execute("COMMIT")
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise MigrationError(f"schema v1 creation failed: {error}") from error

    @staticmethod
    def _read_schema_meta(connection: sqlite3.Connection) -> tuple[int, int]:
        table_row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
        ).fetchone()
        if table_row is None:
            raise MigrationError("nonempty SQLite store is missing schema_meta")
        try:
            rows = connection.execute(
                "SELECT schema_version, created_at_ms FROM schema_meta"
            ).fetchall()
        except sqlite3.Error as error:
            raise CorruptStoreError(f"cannot read schema_meta: {error}") from error
        if len(rows) != 1:
            raise MigrationError("schema_meta must contain exactly one authority row")
        version, created_at_ms = rows[0]
        if not isinstance(version, int) or not isinstance(created_at_ms, int):
            raise MigrationError("schema_meta contains invalid values")
        if version > _SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"schema version {version} is newer than supported version {_SCHEMA_VERSION}"
            )
        if version != _SCHEMA_VERSION:
            raise MigrationError(f"unsupported schema version {version}")
        if created_at_ms < 0:
            raise MigrationError("schema creation timestamp is negative")
        return version, created_at_ms

    @staticmethod
    def _validate_schema_sql(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT type, name, sql FROM sqlite_schema
            WHERE name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        actual = {
            (str(object_type), str(name)): str(sql)
            for object_type, name, sql in rows
            if sql is not None
        }
        if set(actual) != set(_EXPECTED_SCHEMA_SQL):
            raise MigrationError("schema v1 objects do not match the required artifact set")
        for identity, expected_sql in _EXPECTED_SCHEMA_SQL.items():
            if _normalize_sql(actual[identity]) != _normalize_sql(expected_sql):
                raise MigrationError(
                    f"schema v1 object differs from contract: {identity[1]}"
                )

    @staticmethod
    def _run_quick_check(connection: sqlite3.Connection) -> None:
        try:
            rows = connection.execute("PRAGMA quick_check").fetchall()
        except sqlite3.Error as error:
            raise CorruptStoreError(f"SQLite quick_check failed: {error}") from error
        if rows != [("ok",)]:
            details = "; ".join(str(row[0]) for row in rows)
            raise CorruptStoreError(f"SQLite quick_check reported: {details[:1024]}")

    @staticmethod
    def _read_pragmas(connection: sqlite3.Connection) -> PragmaSnapshot:
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
        foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        busy_timeout_ms = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
        return PragmaSnapshot(
            journal_mode=journal_mode.lower(),
            synchronous=synchronous,
            foreign_keys=foreign_keys,
            busy_timeout_ms=busy_timeout_ms,
        )

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None or self._process_lock is None:
            raise TwinStoreError("store is closed")
        return self._connection

    def _release_resources(self) -> None:
        self._close_connection()
        process_lock = self._process_lock
        self._process_lock = None
        if process_lock is not None:
            fd, _ = process_lock
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        self._store_state = (0, 0, None, None)

    def _close_connection(self) -> None:
        connection = self._connection
        if connection is not None:
            try:
                connection.close()
            except Exception as error:  # pylint: disable=broad-exception-caught
                reason = _bounded_message(f"SQLite close failed: {error}")
                self._set_degradation(reason)
                raise TwinStoreError(reason) from error
            self._connection = None


def _normalize_sql(statement: str) -> str:
    return " ".join(statement.rstrip().rstrip(";").split())


def _bounded_message(message: str) -> str:
    return message[:1024]


def _validate_sqlite_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    if value > _MAX_SQLITE_INTEGER:
        raise OverflowError(f"{name} exceeds SQLite integer range")


def _validate_observed_counter(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _persisted_device_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TwinStoreError(
            f"persisted device {name} must be an exact SQLite integer"
        )
    if not 0 <= value <= _MAX_SQLITE_INTEGER:
        raise TwinStoreError(
            f"persisted device {name} is outside SQLite integer range"
        )
    return value


def _merge_persisted_device_state(
    *,
    normalized_device_id: str,
    row: tuple[object, ...],
    observed_at_ms: int,
    next_wire_id: int,
    next_wire_id_set: int,
) -> DeviceState:
    first_seen_at_ms = _persisted_device_integer("first_seen_at_ms", row[0])
    last_seen_at_ms = _persisted_device_integer("last_seen_at_ms", row[1])
    current_next_wire_id = _persisted_device_integer("next_wire_id", row[2])
    current_next_wire_id_set = _persisted_device_integer("next_wire_id_set", row[3])
    if last_seen_at_ms < first_seen_at_ms:
        raise TwinStoreError("persisted device timestamps violate monotonic order")
    return DeviceState(
        device_id=normalized_device_id,
        first_seen_at_ms=first_seen_at_ms,
        last_seen_at_ms=max(last_seen_at_ms, observed_at_ms),
        next_wire_id=max(current_next_wire_id, next_wire_id),
        next_wire_id_set=max(current_next_wire_id_set, next_wire_id_set),
    )


def _validate_device_id(device_id: str) -> str:
    if not isinstance(device_id, str):
        raise ValueError("device_id must be a string")
    normalized = device_id.strip()
    if normalized != device_id or not normalized:
        raise ValueError("device_id must be non-empty and normalized")
    if len(normalized) > 128:
        raise ValueError("device_id exceeds 128 characters")
    return normalized
