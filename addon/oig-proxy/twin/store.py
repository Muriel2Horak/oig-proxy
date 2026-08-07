"""Fail-closed SQLite source of truth for local-setting transactions."""
# pylint: disable=too-many-lines,too-many-public-methods

from __future__ import annotations

import fcntl
import hashlib
from datetime import datetime
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
from typing import Any
import uuid
from zoneinfo import ZoneInfo

from settings_constraints import validate_setting_value

from .ack_parser import (
    SettingEvent,
    SettingResponse,
    derive_event_evidence_id,
    parse_setting_event_content,
)
from .state import (
    AckResult,
    AttemptRenderContext,
    AttemptRenderer,
    AttemptWriteOutcome,
    ClaimDisposition,
    ClaimResult,
    CommandAttempt,
    CommandState,
    CommandTransition,
    ConfirmedSetting,
    ControlIngress,
    ControlPolicy,
    DeviceState,
    EnqueueResult,
    EventDisposition,
    EventMatchResult,
    EventTimeoutCandidate,
    IngressDisposition,
    NackResult,
    PragmaSnapshot,
    RenderedAttempt,
    RecoveryReport,
    RetryReason,
    SettingEventReceipt,
    StoreStatus,
    SweepReport,
    TERMINAL_STATES,
    TransitionAuditSnapshot,
    TwinCommand,
)


_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MS = 5000
_MAX_SQLITE_INTEGER = (1 << 63) - 1
_MAX_WIRE_FRAME_BYTES = 1_048_576
_PRAGUE = ZoneInfo("Europe/Prague")
_FIVE_DIGITS = re.compile(r"[0-9]{5}")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
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
    device_id TEXT NOT NULL,
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
        except BaseException as error:  # pylint: disable=broad-exception-caught
            rollback_error: BaseException | None = None
            try:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
            except BaseException as caught:  # pylint: disable=broad-exception-caught
                rollback_error = caught
            if rollback_error is None and not isinstance(error, Exception):
                raise
            if isinstance(error, TwinStoreError):
                reason = str(error)
            else:
                reason = f"failed to observe device: {error}"
            if rollback_error is not None:
                reason = f"{reason}; rollback failed: {rollback_error}"
            bounded_reason = _bounded_message(reason)
            self._set_degradation(bounded_reason)
            raise TwinStoreError(bounded_reason) from (rollback_error or error)

    def read_device(self, device_id: str) -> DeviceState:
        """Return one frozen committed device snapshot."""
        normalized_device_id = _validate_device_id(device_id)
        with self._mutex:
            self.verify_health()
            row = self._require_connection().execute(
                """
                SELECT device_id, first_seen_at_ms, last_seen_at_ms,
                       next_wire_id, next_wire_id_set
                FROM devices WHERE device_id = ?
                """,
                (normalized_device_id,),
            ).fetchone()
            if row is None:
                raise StoreRecordNotFound(
                    f"device record not found: {normalized_device_id}"
                )
            try:
                return _device_from_row(row)
            except TwinStoreError as error:
                self._set_degradation(str(error))
                raise

    def read_command(self, command_id: str) -> TwinCommand:
        """Return one frozen committed command snapshot."""
        normalized_command_id = _validate_identifier("command_id", command_id)
        with self._mutex:
            self.verify_health()
            return self._read_command_locked(
                self._require_connection(), normalized_command_id
            )

    def read_attempt(
        self, command_id: str, attempt_number: int
    ) -> CommandAttempt:
        """Return one frozen committed attempt snapshot."""
        normalized_command_id = _validate_identifier("command_id", command_id)
        _validate_attempt_number(attempt_number)
        with self._mutex:
            self.verify_health()
            return self._read_attempt_locked(
                self._require_connection(),
                normalized_command_id,
                attempt_number,
            )

    def read_ingress(self, ingress_id: str) -> ControlIngress:
        """Return one frozen committed ingress audit snapshot."""
        normalized_ingress_id = _validate_identifier("ingress_id", ingress_id)
        with self._mutex:
            self.verify_health()
            row = self._require_connection().execute(
                """
                SELECT ingress_id, received_at_ms, topic, topic_device_id,
                       retain, raw_text, disposition, reason, command_id, audit_id
                FROM control_ingress_audit WHERE ingress_id = ?
                """,
                (normalized_ingress_id,),
            ).fetchone()
            if row is None:
                raise StoreRecordNotFound(
                    f"ingress record not found: {normalized_ingress_id}"
                )
            try:
                return _ingress_from_row(row)
            except TwinStoreError as error:
                self._set_degradation(str(error))
                raise

    def read_latest_ingress(self) -> ControlIngress:
        """Return the newest committed ingress, deterministically ordered."""
        with self._mutex:
            self.verify_health()
            row = self._require_connection().execute(
                """
                SELECT ingress_id, received_at_ms, topic, topic_device_id,
                       retain, raw_text, disposition, reason, command_id, audit_id
                FROM control_ingress_audit
                ORDER BY received_at_ms DESC, ingress_id DESC LIMIT 1
                """
            ).fetchone()
            if row is None:
                raise StoreRecordNotFound("no ingress records exist")
            try:
                return _ingress_from_row(row)
            except TwinStoreError as error:
                self._set_degradation(str(error))
                raise

    def read_transitions(
        self, command_id: str | None = None
    ) -> tuple[CommandTransition, ...]:
        """Return frozen committed transitions in monotonic ID order."""
        normalized_command_id = (
            _validate_identifier("command_id", command_id)
            if command_id is not None
            else None
        )
        with self._mutex:
            self.verify_health()
            connection = self._require_connection()
            if normalized_command_id is None:
                rows = connection.execute(
                    """
                    SELECT transition_id, command_id, audit_id, from_state,
                           to_state, occurred_at_ms, attempt_number, session_id,
                           reason, error_text, wire_frame, evidence_frame
                    FROM command_transitions ORDER BY transition_id
                    """
                ).fetchall()
            else:
                if connection.execute(
                    "SELECT 1 FROM commands WHERE command_id = ?",
                    (normalized_command_id,),
                ).fetchone() is None:
                    raise StoreRecordNotFound(
                        f"command record not found: {normalized_command_id}"
                    )
                rows = connection.execute(
                    """
                    SELECT transition_id, command_id, audit_id, from_state,
                           to_state, occurred_at_ms, attempt_number, session_id,
                           reason, error_text, wire_frame, evidence_frame
                    FROM command_transitions
                    WHERE command_id = ? ORDER BY transition_id
                    """,
                    (normalized_command_id,),
                ).fetchall()
            try:
                return tuple(_transition_from_row(row) for row in rows)
            except TwinStoreError as error:
                self._set_degradation(str(error))
                raise

    def record_ingress_disposition(
        self,
        ingress: ControlIngress,
        *,
        disposition: IngressDisposition,
        reason: str,
    ) -> ControlIngress:
        """Persist one rejected or separately handled ingress envelope."""
        normalized = _validate_unpersisted_ingress(ingress)
        if not isinstance(disposition, IngressDisposition):
            raise ValueError("disposition must be an IngressDisposition")
        if disposition is IngressDisposition.ACCEPTED_COMMAND:
            raise ValueError("accepted commands must use enqueue_command")
        bounded_reason = _validate_bounded_text("reason", reason, 1024)

        def operation(connection: sqlite3.Connection) -> ControlIngress:
            _ensure_ingress_absent(connection, normalized.ingress_id)
            connection.execute(
                """
                INSERT INTO control_ingress_audit(
                    ingress_id, received_at_ms, topic, topic_device_id, retain,
                    disposition, reason, raw_text, command_id, audit_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    normalized.ingress_id,
                    normalized.received_at_ms,
                    normalized.topic,
                    normalized.topic_device_id,
                    int(normalized.retain),
                    disposition.value,
                    bounded_reason,
                    normalized.raw_text,
                ),
            )
            return ControlIngress(
                normalized.ingress_id,
                normalized.received_at_ms,
                normalized.topic,
                normalized.topic_device_id,
                normalized.retain,
                normalized.raw_text,
                disposition,
                bounded_reason,
                None,
                None,
            )

        return self._run_mutation("record ingress disposition", operation)

    def record_proxy_control_ingress(
        self, ingress: ControlIngress, *, reason: str
    ) -> ControlIngress:
        """Persist one accepted proxy-control ingress without a BOX command."""
        return self.record_ingress_disposition(
            ingress,
            disposition=IngressDisposition.ACCEPTED_PROXY_CONTROL,
            reason=reason,
        )

    # pylint: disable-next=too-many-arguments
    def enqueue_command(
        self,
        ingress: ControlIngress,
        *,
        device_id: str,
        table_name: str,
        item_name: str,
        value_text: str,
    ) -> EnqueueResult:
        """Atomically persist accepted ingress, replacement, and new command."""
        normalized_ingress = _validate_unpersisted_ingress(ingress)
        normalized_device_id = _validate_device_id(device_id)
        normalized_table = _validate_bounded_text("table_name", table_name, 128)
        normalized_item = _validate_bounded_text("item_name", item_name, 128)
        normalized_value = _validate_bounded_text("value_text", value_text, 1024)
        pending_expires_at_ms = _checked_add_milliseconds(
            "pending_expires_at_ms",
            normalized_ingress.received_at_ms,
            self._policy.pending_ttl_ms,
        )

        def operation(connection: sqlite3.Connection) -> EnqueueResult:
            _ensure_ingress_absent(connection, normalized_ingress.ingress_id)
            if connection.execute(
                "SELECT 1 FROM devices WHERE device_id = ?",
                (normalized_device_id,),
            ).fetchone() is None:
                raise StoreRecordNotFound(
                    f"device record not found: {normalized_device_id}"
                )

            snapshots: list[TransitionAuditSnapshot] = []
            superseded: TwinCommand | None = None
            replace_row = connection.execute(
                """
                SELECT command_id FROM commands
                WHERE device_id = ? AND table_name = ? AND item_name = ?
                  AND state = 'pending' AND attempt_count = 0
                ORDER BY created_at_ms, command_id LIMIT 1
                """,
                (normalized_device_id, normalized_table, normalized_item),
            ).fetchone()
            if replace_row is not None:
                replace_id = str(replace_row[0])
                previous = self._read_command_locked(connection, replace_id)
                replace_cursor = connection.execute(
                    """
                    UPDATE commands
                    SET state = 'superseded', updated_at_ms = ?, completed_at_ms = ?,
                        active_session_id = NULL
                    WHERE command_id = ? AND state = 'pending' AND attempt_count = 0
                    """,
                    (
                        normalized_ingress.received_at_ms,
                        normalized_ingress.received_at_ms,
                        replace_id,
                    ),
                )
                if replace_cursor.rowcount != 1:
                    raise TwinStoreError("unsent replacement CAS failed")
                transition = self._insert_transition_locked(
                    connection,
                    command_id=replace_id,
                    audit_id=previous.audit_id,
                    from_state=CommandState.PENDING,
                    to_state=CommandState.SUPERSEDED,
                    occurred_at_ms=normalized_ingress.received_at_ms,
                    reason="replaced_unsent",
                )
                superseded = self._read_command_locked(connection, replace_id)
                snapshots.append(
                    TransitionAuditSnapshot(superseded, transition)
                )

            predecessor_row = connection.execute(
                """
                SELECT command_id FROM commands
                WHERE device_id = ? AND table_name = ? AND item_name = ?
                  AND state <> 'superseded'
                ORDER BY created_at_ms DESC, command_id DESC LIMIT 1
                """,
                (normalized_device_id, normalized_table, normalized_item),
            ).fetchone()
            predecessor_id = (
                str(predecessor_row[0]) if predecessor_row is not None else None
            )
            command_id = f"cmd-{uuid.uuid4().hex}"
            audit_id = f"audit-{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO commands(
                    command_id, audit_id, device_id, table_name, item_name,
                    value_text, raw_ingress_text, state, created_at_ms,
                    updated_at_ms, pending_expires_at_ms, predecessor_command_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    command_id,
                    audit_id,
                    normalized_device_id,
                    normalized_table,
                    normalized_item,
                    normalized_value,
                    normalized_ingress.raw_text,
                    normalized_ingress.received_at_ms,
                    normalized_ingress.received_at_ms,
                    pending_expires_at_ms,
                    predecessor_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO control_ingress_audit(
                    ingress_id, received_at_ms, topic, topic_device_id, retain,
                    disposition, reason, raw_text, command_id, audit_id
                ) VALUES (?, ?, ?, ?, ?, 'accepted_command',
                          'accepted_ingress', ?, ?, ?)
                """,
                (
                    normalized_ingress.ingress_id,
                    normalized_ingress.received_at_ms,
                    normalized_ingress.topic,
                    normalized_ingress.topic_device_id,
                    int(normalized_ingress.retain),
                    normalized_ingress.raw_text,
                    command_id,
                    audit_id,
                ),
            )
            transition = self._insert_transition_locked(
                connection,
                command_id=command_id,
                audit_id=audit_id,
                from_state=None,
                to_state=CommandState.PENDING,
                occurred_at_ms=normalized_ingress.received_at_ms,
                reason="accepted_ingress",
            )
            command = self._read_command_locked(connection, command_id)
            snapshots.append(TransitionAuditSnapshot(command, transition))
            return EnqueueResult(command, superseded, tuple(snapshots))

        return self._run_mutation("enqueue command", operation)

    def prepare_next_attempt(
        self,
        *,
        device_id: str,
        session_id: str,
        prepared_at_ms: int,
        render: AttemptRenderer,
    ) -> ClaimResult:
        """Select and durably prepare the oldest eligible device command."""
        normalized_device_id = _validate_device_id(device_id)
        normalized_session_id = _validate_identifier("session_id", session_id)
        if len(normalized_session_id) > 128:
            raise ValueError("session_id exceeds 128 characters")
        _validate_sqlite_integer("prepared_at_ms", prepared_at_ms)
        if not callable(render):
            raise ValueError("render must be callable")

        return self._run_mutation(
            "prepare next attempt",
            lambda connection: self._prepare_next_attempt_locked(
                connection,
                device_id=normalized_device_id,
                session_id=normalized_session_id,
                prepared_at_ms=prepared_at_ms,
                render=render,
            ),
        )

    # pylint: disable-next=too-many-arguments
    def _prepare_next_attempt_locked(  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
        self,
        connection: sqlite3.Connection,
        *,
        device_id: str,
        session_id: str,
        prepared_at_ms: int,
        render: AttemptRenderer,
    ) -> ClaimResult:
        active = connection.execute(
            """
            SELECT command_id, active_session_id FROM commands
            WHERE device_id = ? AND state = 'awaiting_ack'
            """,
            (device_id,),
        ).fetchone()
        if active is not None:
            disposition = (
                ClaimDisposition.ACTIVE_DELIVERY_ELSEWHERE
                if str(active[1]) != session_id
                else ClaimDisposition.NO_ELIGIBLE
            )
            return ClaimResult(disposition, None, None)

        candidate_rows = connection.execute(
            """
            SELECT command_id FROM commands
            WHERE device_id = ? AND state IN ('pending', 'retry_pending')
              AND attempt_count < ?
            ORDER BY created_at_ms, command_id
            """,
            (device_id, self._policy.max_attempts),
        ).fetchall()
        command: TwinCommand | None = None
        for candidate_row in candidate_rows:
            candidate = self._read_command_locked(
                connection, str(candidate_row[0])
            )
            if candidate.predecessor_command_id is not None:
                predecessor = connection.execute(
                    "SELECT state FROM commands WHERE command_id = ?",
                    (candidate.predecessor_command_id,),
                ).fetchone()
                if predecessor is None:
                    raise TwinStoreError("command predecessor disappeared")
                if CommandState(str(predecessor[0])) in {
                    CommandState.PENDING,
                    CommandState.RETRY_PENDING,
                    CommandState.AWAITING_ACK,
                }:
                    continue
            identical_wait = connection.execute(
                """
                SELECT 1 FROM commands
                WHERE device_id = ? AND table_name = ? AND item_name = ?
                  AND value_text = ? AND state = 'awaiting_event'
                  AND (created_at_ms < ? OR
                       (created_at_ms = ? AND command_id < ?))
                LIMIT 1
                """,
                (
                    candidate.device_id,
                    candidate.table_name,
                    candidate.item_name,
                    candidate.value_text,
                    candidate.created_at_ms,
                    candidate.created_at_ms,
                    candidate.command_id,
                ),
            ).fetchone()
            if identical_wait is not None:
                continue
            command = candidate
            break
        if command is None:
            return ClaimResult(ClaimDisposition.NO_ELIGIBLE, None, None)

        attempt_number = command.attempt_count + 1
        used_ver_texts = tuple(
            str(row[0])
            for row in connection.execute(
                """
                SELECT ver_text FROM command_attempts
                WHERE command_id = ? ORDER BY attempt_number
                """,
                (command.command_id,),
            ).fetchall()
        )
        first_attempt = command.attempt_count == 0
        if first_attempt:
            device_row = connection.execute(
                """
                SELECT next_wire_id, next_wire_id_set FROM devices
                WHERE device_id = ?
                """,
                (device_id,),
            ).fetchone()
            if device_row is None:
                raise TwinStoreError("command device disappeared")
            wire_id = _persisted_device_integer("next_wire_id", device_row[0])
            wire_id_set = _persisted_device_integer(
                "next_wire_id_set", device_row[1]
            )
            wire_dt = datetime.fromtimestamp(
                prepared_at_ms / 1000, tz=_PRAGUE
            ).strftime("%Y-%m-%d %H:%M:%S")
        else:
            if (
                command.wire_id is None
                or command.wire_id_set is None
                or command.wire_dt is None
            ):
                raise TwinStoreError("retry command lost stable wire identity")
            wire_id = command.wire_id
            wire_id_set = command.wire_id_set
            wire_dt = command.wire_dt

        context = AttemptRenderContext(
            command=command,
            attempt_number=attempt_number,
            prepared_at_ms=prepared_at_ms,
            wire_id=wire_id,
            wire_id_set=wire_id_set,
            wire_dt=wire_dt,
            used_ver_texts=used_ver_texts,
        )
        try:
            rendered = render(context)
            _validate_rendered_attempt(rendered, used_ver_texts)
        except Exception as error:  # pylint: disable=broad-exception-caught
            bounded_error = _bounded_message(f"render failed: {error}")
            connection.execute(
                """
                UPDATE commands
                SET state = 'failed', updated_at_ms = ?, completed_at_ms = ?,
                    active_session_id = NULL, ack_deadline_ms = NULL,
                    last_error = ?
                WHERE command_id = ? AND state IN ('pending', 'retry_pending')
                  AND attempt_count = ?
                """,
                (
                    prepared_at_ms,
                    prepared_at_ms,
                    bounded_error,
                    command.command_id,
                    command.attempt_count,
                ),
            )
            transition = self._insert_transition_locked(
                connection,
                command_id=command.command_id,
                audit_id=command.audit_id,
                from_state=command.state,
                to_state=CommandState.FAILED,
                occurred_at_ms=prepared_at_ms,
                attempt_number=attempt_number,
                session_id=session_id,
                reason="render_failed",
                error_text=bounded_error,
            )
            failed = self._read_command_locked(connection, command.command_id)
            snapshot = TransitionAuditSnapshot(failed, transition)
            return ClaimResult(
                ClaimDisposition.RENDER_FAILED,
                failed,
                None,
                (snapshot,),
            )

        if first_attempt and (
            wire_id >= _MAX_SQLITE_INTEGER or wire_id_set >= _MAX_SQLITE_INTEGER
        ):
            raise TwinStoreError("device wire counters cannot advance")
        ack_deadline_ms = _checked_add_milliseconds(
            "ack_deadline_ms", prepared_at_ms, self._policy.ack_timeout_ms
        )
        selected_transition = self._insert_transition_locked(
            connection,
            command_id=command.command_id,
            audit_id=command.audit_id,
            from_state=command.state,
            to_state=command.state,
            occurred_at_ms=prepared_at_ms,
            attempt_number=attempt_number,
            session_id=session_id,
            reason="selected",
        )
        connection.execute(
            """
            INSERT INTO command_attempts(
                command_id, attempt_number, session_id, prepared_at_ms,
                ack_deadline_ms, tsec_text, ver_text, crc_text, wire_frame,
                wire_length, write_outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared')
            """,
            (
                command.command_id,
                attempt_number,
                session_id,
                prepared_at_ms,
                ack_deadline_ms,
                rendered.tsec_text,
                rendered.ver_text,
                rendered.crc_text,
                rendered.wire_frame,
                len(rendered.wire_frame),
            ),
        )
        if first_attempt:
            connection.execute(
                """
                UPDATE devices SET next_wire_id = ?, next_wire_id_set = ?
                WHERE device_id = ?
                """,
                (wire_id + 1, wire_id_set + 1, device_id),
            )
        cursor = connection.execute(
            """
            UPDATE commands
            SET state = 'awaiting_ack', updated_at_ms = ?, wire_id = ?,
                wire_id_set = ?, wire_dt = ?, attempt_count = ?,
                active_session_id = ?, ack_deadline_ms = ?,
                event_deadline_ms = NULL, acked_at_ms = NULL,
                ack_device_rdt = NULL, completed_at_ms = NULL,
                last_wire_frame = ?, last_error = NULL
            WHERE command_id = ? AND state = ? AND attempt_count = ?
            """,
            (
                prepared_at_ms,
                wire_id,
                wire_id_set,
                wire_dt,
                attempt_number,
                session_id,
                ack_deadline_ms,
                rendered.wire_frame,
                command.command_id,
                command.state.value,
                command.attempt_count,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleAttemptError("command claim CAS no longer matches")
        prepared_transition = self._insert_transition_locked(
            connection,
            command_id=command.command_id,
            audit_id=command.audit_id,
            from_state=command.state,
            to_state=CommandState.AWAITING_ACK,
            occurred_at_ms=prepared_at_ms,
            attempt_number=attempt_number,
            session_id=session_id,
            reason="attempt_prepared",
            wire_frame=rendered.wire_frame,
        )
        updated_command = self._read_command_locked(connection, command.command_id)
        attempt = self._read_attempt_locked(
            connection, command.command_id, attempt_number
        )
        snapshots = (
            TransitionAuditSnapshot(updated_command, selected_transition),
            TransitionAuditSnapshot(updated_command, prepared_transition, attempt),
        )
        return ClaimResult(
            ClaimDisposition.PREPARED,
            updated_command,
            attempt,
            snapshots,
        )

    # pylint: disable-next=too-many-arguments
    def acknowledge_and_prepare_next(
        self,
        *,
        command_id: str,
        attempt_number: int,
        session_id: str,
        response: SettingResponse,
        received_at_ms: int,
        evidence_frame: bytes,
        render: AttemptRenderer,
    ) -> AckResult:
        """Accept one exact ACK and atomically prepare the next device command."""
        normalized_command_id, normalized_session_id = _validate_response_inputs(
            command_id=command_id,
            attempt_number=attempt_number,
            session_id=session_id,
            response=response,
            expected_result="ACK",
            received_at_ms=received_at_ms,
            evidence_frame=evidence_frame,
        )
        if not callable(render):
            raise ValueError("render must be callable")
        event_deadline_ms = _checked_add_milliseconds(
            "event_deadline_ms", received_at_ms, self._policy.event_timeout_ms
        )

        def operation(connection: sqlite3.Connection) -> AckResult:
            if self._response_is_duplicate_locked(
                connection,
                command_id=normalized_command_id,
                attempt_number=attempt_number,
                session_id=normalized_session_id,
                fingerprint=response.fingerprint,
            ):
                return AckResult(
                    None,
                    True,
                    ClaimResult(ClaimDisposition.NO_ELIGIBLE, None, None),
                )
            command, attempt = self._require_active_attempt_locked(
                connection,
                command_id=normalized_command_id,
                attempt_number=attempt_number,
                session_id=normalized_session_id,
            )
            _validate_response_attempt_window(attempt, received_at_ms)
            self._reject_decreasing_response_rdt_locked(
                connection,
                session_id=normalized_session_id,
                response_rdt=response.rdt_text,
            )
            cursor = connection.execute(
                """
                UPDATE command_attempts
                SET response_fingerprint = ?, response_rdt = ?
                WHERE command_id = ? AND attempt_number = ? AND session_id = ?
                  AND response_fingerprint IS NULL
                """,
                (
                    response.fingerprint,
                    response.rdt_text,
                    normalized_command_id,
                    attempt_number,
                    normalized_session_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleAttemptError("attempt response no longer matches")
            cursor = connection.execute(
                """
                UPDATE commands
                SET state = 'awaiting_event', updated_at_ms = ?,
                    active_session_id = NULL, event_deadline_ms = ?,
                    acked_at_ms = ?, ack_device_rdt = ?, last_error = NULL
                WHERE command_id = ? AND state = 'awaiting_ack'
                  AND attempt_count = ? AND active_session_id = ?
                """,
                (
                    received_at_ms,
                    event_deadline_ms,
                    received_at_ms,
                    response.rdt_text,
                    normalized_command_id,
                    attempt_number,
                    normalized_session_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleAttemptError("ACK command ownership no longer matches")
            transition = self._insert_transition_locked(
                connection,
                command_id=normalized_command_id,
                audit_id=command.audit_id,
                from_state=CommandState.AWAITING_ACK,
                to_state=CommandState.AWAITING_EVENT,
                occurred_at_ms=received_at_ms,
                attempt_number=attempt_number,
                session_id=normalized_session_id,
                reason="ack_received",
                evidence_frame=evidence_frame,
            )
            accepted = self._read_command_locked(connection, normalized_command_id)
            accepted_attempt = self._read_attempt_locked(
                connection, normalized_command_id, attempt_number
            )
            ack_snapshot = TransitionAuditSnapshot(
                accepted, transition, accepted_attempt
            )
            next_claim = self._prepare_next_attempt_locked(
                connection,
                device_id=command.device_id,
                session_id=normalized_session_id,
                prepared_at_ms=received_at_ms,
                render=render,
            )
            return AckResult(
                accepted,
                False,
                next_claim,
                (ack_snapshot, *next_claim.snapshots),
            )

        return self._run_mutation("acknowledge response", operation)

    # pylint: disable-next=too-many-arguments
    def mark_nack(
        self,
        *,
        command_id: str,
        attempt_number: int,
        session_id: str,
        response: SettingResponse,
        received_at_ms: int,
        evidence_frame: bytes,
    ) -> NackResult:
        """Accept one exact NACK as a terminal command failure."""
        normalized_command_id, normalized_session_id = _validate_response_inputs(
            command_id=command_id,
            attempt_number=attempt_number,
            session_id=session_id,
            response=response,
            expected_result="NACK",
            received_at_ms=received_at_ms,
            evidence_frame=evidence_frame,
        )
        diagnostic = _bounded_message(response.reason or "NACK")

        def operation(connection: sqlite3.Connection) -> NackResult:
            if self._response_is_duplicate_locked(
                connection,
                command_id=normalized_command_id,
                attempt_number=attempt_number,
                session_id=normalized_session_id,
                fingerprint=response.fingerprint,
            ):
                return NackResult(None, True)
            command, attempt = self._require_active_attempt_locked(
                connection,
                command_id=normalized_command_id,
                attempt_number=attempt_number,
                session_id=normalized_session_id,
            )
            _validate_response_attempt_window(attempt, received_at_ms)
            self._reject_decreasing_response_rdt_locked(
                connection,
                session_id=normalized_session_id,
                response_rdt=response.rdt_text,
            )
            attempt_cursor = connection.execute(
                """
                UPDATE command_attempts
                SET response_fingerprint = ?, response_rdt = ?
                WHERE command_id = ? AND attempt_number = ? AND session_id = ?
                  AND response_fingerprint IS NULL
                """,
                (
                    response.fingerprint,
                    response.rdt_text,
                    normalized_command_id,
                    attempt_number,
                    normalized_session_id,
                ),
            )
            if attempt_cursor.rowcount != 1:
                raise StaleAttemptError("attempt response no longer matches")
            cursor = connection.execute(
                """
                UPDATE commands
                SET state = 'failed', updated_at_ms = ?, completed_at_ms = ?,
                    active_session_id = NULL, last_error = ?
                WHERE command_id = ? AND state = 'awaiting_ack'
                  AND attempt_count = ? AND active_session_id = ?
                """,
                (
                    received_at_ms,
                    received_at_ms,
                    diagnostic,
                    normalized_command_id,
                    attempt_number,
                    normalized_session_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleAttemptError("NACK command ownership no longer matches")
            transition = self._insert_transition_locked(
                connection,
                command_id=normalized_command_id,
                audit_id=command.audit_id,
                from_state=CommandState.AWAITING_ACK,
                to_state=CommandState.FAILED,
                occurred_at_ms=received_at_ms,
                attempt_number=attempt_number,
                session_id=normalized_session_id,
                reason="nack_received",
                error_text=diagnostic,
                evidence_frame=evidence_frame,
            )
            accepted = self._read_command_locked(connection, normalized_command_id)
            accepted_attempt = self._read_attempt_locked(
                connection, normalized_command_id, attempt_number
            )
            return NackResult(
                accepted,
                False,
                (
                    TransitionAuditSnapshot(
                        accepted, transition, accepted_attempt
                    ),
                ),
            )

        return self._run_mutation("record NACK", operation)

    @staticmethod
    def _response_is_duplicate_locked(
        connection: sqlite3.Connection,
        *,
        command_id: str,
        attempt_number: int,
        session_id: str,
        fingerprint: str,
    ) -> bool:
        rows = connection.execute(
            """
            SELECT command_id, attempt_number, session_id FROM command_attempts
            WHERE session_id = ? AND response_fingerprint = ?
            """,
            (session_id, fingerprint),
        ).fetchall()
        if not rows:
            return False
        if any(
            row == (command_id, attempt_number, session_id)
            for row in rows
        ):
            return True
        raise StaleAttemptError(
            "response evidence belongs to a different command attempt"
        )

    @staticmethod
    def _reject_decreasing_response_rdt_locked(
        connection: sqlite3.Connection,
        *,
        session_id: str,
        response_rdt: str | None,
    ) -> None:
        parsed_response = _parse_protocol_datetime(response_rdt)
        if parsed_response is None:
            return
        prior_values = (
            _parse_protocol_datetime(str(row[0]))
            for row in connection.execute(
                """
                SELECT response_rdt FROM command_attempts
                WHERE session_id = ? AND response_rdt IS NOT NULL
                """,
                (session_id,),
            ).fetchall()
        )
        if any(
            prior is not None and parsed_response < prior
            for prior in prior_values
        ):
            raise StaleAttemptError("response Rdt decreased within session batch")

    def record_event(
        self,
        *,
        evidence: SettingEvent,
        received_at_ms: int,
        evidence_frame: bytes,
        active_session_id: str | None = None,
    ) -> EventMatchResult:
        """Persist immutable evidence and confirm at most one exact command."""
        normalized_evidence, content_is_consistent = _validate_setting_event(evidence)
        _validate_sqlite_integer("received_at_ms", received_at_ms)
        normalized_frame = _validate_evidence_frame(evidence_frame)
        normalized_active_session_id = (
            _validate_identifier("active_session_id", active_session_id)
            if active_session_id is not None
            else None
        )

        def operation(connection: sqlite3.Connection) -> EventMatchResult:
            duplicate_row = connection.execute(
                "SELECT * FROM event_receipts WHERE evidence_id = ?",
                (normalized_evidence.evidence_id,),
            ).fetchone()
            if duplicate_row is not None:
                connection.execute(
                    """
                    UPDATE event_receipts
                    SET duplicate_count = duplicate_count + 1,
                        last_seen_at_ms = MAX(last_seen_at_ms, ?)
                    WHERE evidence_id = ?
                    """,
                    (received_at_ms, normalized_evidence.evidence_id),
                )
                receipt = self._read_event_receipt_locked(
                    connection, normalized_evidence.evidence_id
                )
                return EventMatchResult(
                    EventDisposition.DUPLICATE,
                    None,
                    None,
                    None,
                    receipt,
                    None,
                    None,
                )

            connection.execute(
                """
                INSERT INTO event_receipts(
                    evidence_id, received_at_ms, device_id, event_id_set,
                    device_dt, table_name, item_name, old_value_text,
                    new_value_text, evidence_frame, disposition, command_id,
                    duplicate_count, last_seen_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unmatched', NULL, 0, ?)
                """,
                (
                    normalized_evidence.evidence_id,
                    received_at_ms,
                    normalized_evidence.device_id,
                    normalized_evidence.event_id_set,
                    normalized_evidence.device_dt,
                    normalized_evidence.table_name,
                    normalized_evidence.item_name,
                    normalized_evidence.old_value_text,
                    normalized_evidence.new_value_text,
                    normalized_frame,
                    received_at_ms,
                ),
            )
            if not content_is_consistent:
                receipt = self._read_event_receipt_locked(
                    connection, normalized_evidence.evidence_id
                )
                return EventMatchResult(
                    EventDisposition.UNMATCHED,
                    None,
                    None,
                    None,
                    receipt,
                    None,
                    None,
                )
            value_result = validate_setting_value(
                normalized_evidence.table_name,
                normalized_evidence.item_name,
                normalized_evidence.new_value_text,
            )
            if not value_result.accepted or value_result.value_text is None:
                receipt = self._read_event_receipt_locked(
                    connection, normalized_evidence.evidence_id
                )
                return EventMatchResult(
                    EventDisposition.UNMATCHED,
                    None,
                    None,
                    None,
                    receipt,
                    None,
                    None,
                )

            candidate = self._find_event_match_locked(
                connection,
                evidence=normalized_evidence,
                canonical_value=value_result.value_text,
                received_at_ms=received_at_ms,
                active_session_id=normalized_active_session_id,
            )
            if candidate is None:
                receipt = self._read_event_receipt_locked(
                    connection, normalized_evidence.evidence_id
                )
                return EventMatchResult(
                    EventDisposition.UNMATCHED,
                    None,
                    None,
                    None,
                    receipt,
                    None,
                    None,
                )

            command, prior_state, active_session_id = candidate
            cursor = connection.execute(
                """
                UPDATE commands
                SET state = 'confirmed', updated_at_ms = ?, completed_at_ms = ?,
                    active_session_id = NULL, last_error = NULL
                WHERE command_id = ? AND state = ?
                """,
                (
                    received_at_ms,
                    received_at_ms,
                    command.command_id,
                    prior_state.value,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleAttemptError("event match command state changed")
            receipt_cursor = connection.execute(
                """
                UPDATE event_receipts
                SET disposition = 'confirmed', command_id = ?
                WHERE evidence_id = ? AND disposition = 'unmatched'
                  AND command_id IS NULL
                """,
                (command.command_id, normalized_evidence.evidence_id),
            )
            if receipt_cursor.rowcount != 1:
                raise TwinStoreError("event receipt confirmation CAS failed")
            transition = self._insert_transition_locked(
                connection,
                command_id=command.command_id,
                audit_id=command.audit_id,
                from_state=prior_state,
                to_state=CommandState.CONFIRMED,
                occurred_at_ms=received_at_ms,
                attempt_number=command.attempt_count,
                session_id=active_session_id,
                reason="event_confirmed",
                evidence_frame=normalized_frame,
            )
            confirmed_command = self._read_command_locked(
                connection, command.command_id
            )
            receipt = self._read_event_receipt_locked(
                connection, normalized_evidence.evidence_id
            )
            confirmation = ConfirmedSetting(
                command_id=confirmed_command.command_id,
                audit_id=confirmed_command.audit_id,
                evidence_id=receipt.evidence_id,
                device_id=confirmed_command.device_id,
                table_name=confirmed_command.table_name,
                item_name=confirmed_command.item_name,
                value_text=confirmed_command.value_text,
                confirmed_at_ms=received_at_ms,
            )
            snapshot = TransitionAuditSnapshot(
                confirmed_command,
                transition,
                self._read_attempt_locked(
                    connection,
                    confirmed_command.command_id,
                    confirmed_command.attempt_count,
                ),
                receipt,
            )
            return EventMatchResult(
                EventDisposition.CONFIRMED,
                confirmed_command,
                prior_state,
                active_session_id,
                receipt,
                confirmation,
                snapshot,
            )

        return self._run_mutation("record setting event", operation)

    def _find_event_match_locked(
        self,
        connection: sqlite3.Connection,
        *,
        evidence: SettingEvent,
        canonical_value: str,
        received_at_ms: int,
        active_session_id: str | None,
    ) -> tuple[TwinCommand, CommandState, str | None] | None:
        awaiting_rows = connection.execute(
            """
            SELECT command_id FROM commands
            WHERE device_id = ? AND table_name = ? AND item_name = ?
              AND value_text = ? AND state = 'awaiting_event'
              AND acked_at_ms <= ? AND event_deadline_ms >= ?
            ORDER BY created_at_ms, command_id
            """,
            (
                evidence.device_id,
                evidence.table_name,
                evidence.item_name,
                canonical_value,
                received_at_ms,
                received_at_ms,
            ),
        ).fetchall()
        parsed_event_dt = _parse_protocol_datetime(evidence.device_dt)
        for row in awaiting_rows:
            command = self._read_command_locked(connection, str(row[0]))
            parsed_ack_rdt = _parse_protocol_datetime(command.ack_device_rdt)
            if (
                parsed_event_dt is not None
                and parsed_ack_rdt is not None
                and parsed_event_dt < parsed_ack_rdt
            ):
                continue
            return command, CommandState.AWAITING_EVENT, command.active_session_id

        active_rows = connection.execute(
            """
            SELECT c.command_id, a.prepared_at_ms
            FROM commands AS c
            JOIN command_attempts AS a
              ON a.command_id = c.command_id
             AND a.attempt_number = c.attempt_count
            WHERE c.device_id = ? AND c.table_name = ? AND c.item_name = ?
              AND c.value_text = ? AND c.state = 'awaiting_ack'
              AND a.prepared_at_ms <= ? AND a.ack_deadline_ms >= ?
              AND (? IS NULL OR c.active_session_id = ?)
            ORDER BY c.created_at_ms, c.command_id
            """,
            (
                evidence.device_id,
                evidence.table_name,
                evidence.item_name,
                canonical_value,
                received_at_ms,
                received_at_ms,
                active_session_id,
                active_session_id,
            ),
        ).fetchall()
        if not active_rows:
            return None
        command = self._read_command_locked(connection, str(active_rows[0][0]))
        return command, CommandState.AWAITING_ACK, command.active_session_id

    def read_event_receipt(self, evidence_id: str) -> SettingEventReceipt:
        """Return one frozen committed event receipt snapshot."""
        normalized_id = _validate_evidence_id(evidence_id)
        with self._mutex:
            self.verify_health()
            return self._read_event_receipt_locked(
                self._require_connection(), normalized_id
            )

    def _read_event_receipt_locked(
        self, connection: sqlite3.Connection, evidence_id: str
    ) -> SettingEventReceipt:
        row = connection.execute(
            "SELECT * FROM event_receipts WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            raise StoreRecordNotFound(
                f"event receipt not found: {evidence_id}"
            )
        try:
            return _event_receipt_from_row(row)
        except TwinStoreError as error:
            self._set_degradation(str(error))
            raise

    def sweep_deadlines(
        self, *, now_ms: int, include_event_timeouts: bool = True
    ) -> SweepReport:
        """Atomically reconcile strict overdue command deadlines."""
        _validate_sqlite_integer("now_ms", now_ms)
        if type(include_event_timeouts) is not bool:  # pylint: disable=unidiomatic-typecheck
            raise ValueError("include_event_timeouts must be a boolean")

        def operation(connection: sqlite3.Connection) -> SweepReport:
            snapshots: list[TransitionAuditSnapshot] = []
            expired_pending = 0
            retry_pending = 0
            failed_attempt_limit = 0
            incomplete_event_timeout = 0
            pending_ids = connection.execute(
                """
                SELECT command_id FROM commands
                WHERE state = 'pending' AND attempt_count = 0
                  AND pending_expires_at_ms < ?
                ORDER BY pending_expires_at_ms, command_id
                """,
                (now_ms,),
            ).fetchall()
            for row in pending_ids:
                command = self._read_command_locked(connection, str(row[0]))
                snapshot = self._transition_command_state_locked(
                    connection,
                    command=command,
                    to_state=CommandState.EXPIRED,
                    occurred_at_ms=now_ms,
                    reason="pending_ttl_expired",
                    completed=True,
                )
                snapshots.append(snapshot)
                expired_pending += 1

            ack_ids = connection.execute(
                """
                SELECT command_id FROM commands
                WHERE state = 'awaiting_ack' AND ack_deadline_ms < ?
                ORDER BY ack_deadline_ms, command_id
                """,
                (now_ms,),
            ).fetchall()
            for row in ack_ids:
                command = self._read_command_locked(connection, str(row[0]))
                if command.active_session_id is None:
                    raise TwinStoreError("awaiting ACK command lost session ownership")
                snapshot = self._release_active_attempt_locked(
                    connection,
                    command=command,
                    attempt_number=command.attempt_count,
                    session_id=command.active_session_id,
                    occurred_at_ms=now_ms,
                    reason=RetryReason.ACK_TIMEOUT,
                    error=None,
                )
                snapshots.append(snapshot)
                if snapshot.command.state is CommandState.FAILED:
                    failed_attempt_limit += 1
                else:
                    retry_pending += 1

            if include_event_timeouts:
                event_ids = connection.execute(
                    """
                    SELECT command_id FROM commands
                    WHERE state = 'awaiting_event' AND event_deadline_ms < ?
                    ORDER BY event_deadline_ms, command_id
                    """,
                    (now_ms,),
                ).fetchall()
                for row in event_ids:
                    command = self._read_command_locked(
                        connection, str(row[0])
                    )
                    snapshots.append(
                        self._transition_command_state_locked(
                            connection,
                            command=command,
                            to_state=CommandState.INCOMPLETE,
                            occurred_at_ms=now_ms,
                            reason="event_timeout",
                            completed=True,
                        )
                    )
                    incomplete_event_timeout += 1
            snapshots.sort(key=lambda snapshot: snapshot.transition.transition_id)
            return SweepReport(
                expired_pending,
                retry_pending,
                failed_attempt_limit,
                incomplete_event_timeout,
                tuple(snapshots),
            )

        return self._run_mutation("sweep deadlines", operation)

    def read_event_timeout_candidates(
        self, *, now_ms: int
    ) -> tuple[EventTimeoutCandidate, ...]:
        """Return strict-overdue event rows without mutating them."""
        _validate_sqlite_integer("now_ms", now_ms)
        with self._mutex:
            self.verify_health()
            rows = self._require_connection().execute(
                """
                SELECT command_id, device_id, event_deadline_ms FROM commands
                WHERE state = 'awaiting_event' AND event_deadline_ms < ?
                ORDER BY event_deadline_ms, command_id
                """,
                (now_ms,),
            ).fetchall()
            try:
                return tuple(
                    EventTimeoutCandidate(
                        _persisted_identifier(
                            "event timeout command_id", row[0], 256
                        ),
                        _persisted_identifier(
                            "event timeout device_id", row[1], 128
                        ),
                        _persisted_integer("event deadline", row[2]),
                    )
                    for row in rows
                )
            except TwinStoreError as error:
                self._set_degradation(str(error))
                raise

    def mark_event_incomplete(
        self,
        *,
        command_id: str,
        expected_event_deadline_ms: int,
        now_ms: int,
    ) -> TransitionAuditSnapshot | None:
        """Apply the runtime event-timeout mutation through an exact CAS."""
        normalized_command_id = _validate_identifier("command_id", command_id)
        _validate_sqlite_integer(
            "expected_event_deadline_ms", expected_event_deadline_ms
        )
        _validate_sqlite_integer("now_ms", now_ms)

        def operation(
            connection: sqlite3.Connection,
        ) -> TransitionAuditSnapshot | None:
            if now_ms <= expected_event_deadline_ms:
                return None
            row = connection.execute(
                """
                SELECT command_id FROM commands
                WHERE command_id = ? AND state = 'awaiting_event'
                  AND event_deadline_ms = ? AND event_deadline_ms < ?
                """,
                (normalized_command_id, expected_event_deadline_ms, now_ms),
            ).fetchone()
            if row is None:
                return None
            command = self._read_command_locked(connection, normalized_command_id)
            return self._transition_command_state_locked(
                connection,
                command=command,
                to_state=CommandState.INCOMPLETE,
                occurred_at_ms=now_ms,
                reason="event_timeout",
                completed=True,
            )

        return self._run_mutation("mark event incomplete", operation)

    def recover(self, *, now_ms: int) -> RecoveryReport:
        """Reconcile durable startup states after socket ownership was lost."""
        _validate_sqlite_integer("now_ms", now_ms)

        def operation(connection: sqlite3.Connection) -> RecoveryReport:
            expired_pending = 0
            retry_pending = 0
            failed_attempt_limit = 0
            kept_awaiting_event = 0
            incomplete_event_timeout = 0

            pending_rows = connection.execute(
                """
                SELECT command_id FROM commands
                WHERE state = 'pending' AND attempt_count = 0
                  AND pending_expires_at_ms < ?
                ORDER BY pending_expires_at_ms, command_id
                """,
                (now_ms,),
            ).fetchall()
            for row in pending_rows:
                command = self._read_command_locked(connection, str(row[0]))
                self._transition_command_state_locked(
                    connection,
                    command=command,
                    to_state=CommandState.EXPIRED,
                    occurred_at_ms=now_ms,
                    reason="recovery_pending_expired",
                    completed=True,
                )
                expired_pending += 1

            active_rows = connection.execute(
                """
                SELECT command_id FROM commands
                WHERE state = 'awaiting_ack'
                ORDER BY created_at_ms, command_id
                """
            ).fetchall()
            for row in active_rows:
                command = self._read_command_locked(connection, str(row[0]))
                if command.active_session_id is None:
                    raise TwinStoreError("awaiting ACK command lost session ownership")
                snapshot = self._release_active_attempt_locked(
                    connection,
                    command=command,
                    attempt_number=command.attempt_count,
                    session_id=command.active_session_id,
                    occurred_at_ms=now_ms,
                    reason=RetryReason.SHUTDOWN,
                    error="recovered without live socket ownership",
                )
                if snapshot.command.state is CommandState.FAILED:
                    failed_attempt_limit += 1
                else:
                    retry_pending += 1

            exhausted_rows = connection.execute(
                """
                SELECT command_id FROM commands
                WHERE state = 'retry_pending' AND attempt_count >= ?
                ORDER BY created_at_ms, command_id
                """,
                (self._policy.max_attempts,),
            ).fetchall()
            for row in exhausted_rows:
                command = self._read_command_locked(connection, str(row[0]))
                self._transition_command_state_locked(
                    connection,
                    command=command,
                    to_state=CommandState.FAILED,
                    occurred_at_ms=now_ms,
                    reason="recovery_attempt_limit",
                    completed=True,
                    error_text="attempt limit reached during recovery",
                )
                failed_attempt_limit += 1

            event_rows = connection.execute(
                """
                SELECT command_id FROM commands
                WHERE state = 'awaiting_event'
                ORDER BY event_deadline_ms, command_id
                """
            ).fetchall()
            for row in event_rows:
                command = self._read_command_locked(connection, str(row[0]))
                if command.event_deadline_ms is None:
                    raise TwinStoreError("awaiting event command lost deadline")
                if now_ms > command.event_deadline_ms:
                    self._transition_command_state_locked(
                        connection,
                        command=command,
                        to_state=CommandState.INCOMPLETE,
                        occurred_at_ms=now_ms,
                        reason="recovery_event_timeout",
                        completed=True,
                    )
                    incomplete_event_timeout += 1
                else:
                    kept_awaiting_event += 1
            return RecoveryReport(
                expired_pending,
                retry_pending,
                failed_attempt_limit,
                kept_awaiting_event,
                incomplete_event_timeout,
            )

        return self._run_mutation("recover store", operation)

    def status_snapshot(self, device_id: str | None = None) -> StoreStatus:
        """Return exact state counts and fail-closed control availability."""
        normalized_device_id = (
            _validate_device_id(device_id) if device_id is not None else None
        )
        with self._mutex:
            degradation_reason = self._store_state[2]
            if degradation_reason is not None:
                raise TwinStoreError(degradation_reason)
            self.verify_health()
            connection = self._require_connection()
            if normalized_device_id is None:
                rows = connection.execute(
                    "SELECT state, COUNT(*) FROM commands GROUP BY state"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT state, COUNT(*) FROM commands
                    WHERE device_id = ? GROUP BY state
                    """,
                    (normalized_device_id,),
                ).fetchall()
            by_state = {CommandState(str(row[0])): int(row[1]) for row in rows}
            counts = tuple((state, by_state.get(state, 0)) for state in CommandState)
            nonterminal = sum(
                count for state, count in counts if state not in TERMINAL_STATES
            )
            return StoreStatus(counts, nonterminal, True, None)

    def single_nonterminal(
        self, device_id: str | None = None
    ) -> TwinCommand:
        """Return the only committed nonterminal command in the requested scope."""
        normalized_device_id = (
            _validate_device_id(device_id) if device_id is not None else None
        )
        terminal_values = tuple(state.value for state in TERMINAL_STATES)
        placeholders = ",".join("?" for _ in terminal_values)
        with self._mutex:
            self.verify_health()
            connection = self._require_connection()
            parameters: tuple[object, ...]
            if normalized_device_id is None:
                sql = f"""
                    SELECT command_id FROM commands
                    WHERE state NOT IN ({placeholders})
                    ORDER BY created_at_ms, command_id LIMIT 2
                """
                parameters = terminal_values
            else:
                sql = f"""
                    SELECT command_id FROM commands
                    WHERE device_id = ? AND state NOT IN ({placeholders})
                    ORDER BY created_at_ms, command_id LIMIT 2
                """
                parameters = (normalized_device_id, *terminal_values)
            rows = connection.execute(sql, parameters).fetchall()
            if not rows:
                raise StoreRecordNotFound("no nonterminal command exists")
            if len(rows) != 1:
                raise TwinStoreError("more than one nonterminal command exists")
            return self._read_command_locked(connection, str(rows[0][0]))

    # pylint: disable-next=too-many-arguments
    def _transition_command_state_locked(
        self,
        connection: sqlite3.Connection,
        *,
        command: TwinCommand,
        to_state: CommandState,
        occurred_at_ms: int,
        reason: str,
        completed: bool,
        error_text: str | None = None,
    ) -> TransitionAuditSnapshot:
        if command.state in TERMINAL_STATES:
            raise StaleAttemptError("terminal command states are immutable")
        cursor = connection.execute(
            """
            UPDATE commands
            SET state = ?, updated_at_ms = ?, completed_at_ms = ?,
                active_session_id = NULL, last_error = ?
            WHERE command_id = ? AND state = ?
            """,
            (
                to_state.value,
                occurred_at_ms,
                occurred_at_ms if completed else None,
                _bounded_message(error_text) if error_text is not None else None,
                command.command_id,
                command.state.value,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleAttemptError("command state no longer matches")
        transition = self._insert_transition_locked(
            connection,
            command_id=command.command_id,
            audit_id=command.audit_id,
            from_state=command.state,
            to_state=to_state,
            occurred_at_ms=occurred_at_ms,
            attempt_number=command.attempt_count or None,
            session_id=command.active_session_id,
            reason=reason,
            error_text=error_text,
        )
        updated = self._read_command_locked(connection, command.command_id)
        attempt = (
            self._read_attempt_locked(
                connection, command.command_id, command.attempt_count
            )
            if command.attempt_count
            else None
        )
        return TransitionAuditSnapshot(updated, transition, attempt)

    def mark_write_started(
        self,
        *,
        command_id: str,
        attempt_number: int,
        session_id: str,
        started_at_ms: int,
    ) -> TransitionAuditSnapshot:
        """Persist the exact pre-invocation write milestone."""
        return self._mark_write_milestone(
            command_id=command_id,
            attempt_number=attempt_number,
            session_id=session_id,
            occurred_at_ms=started_at_ms,
            expected_outcome=AttemptWriteOutcome.PREPARED,
            new_outcome=AttemptWriteOutcome.STARTED,
            reason="write_started",
            timestamp_column="write_started_at_ms",
        )

    def mark_attempt_drained(
        self,
        *,
        command_id: str,
        attempt_number: int,
        session_id: str,
        drained_at_ms: int,
    ) -> TransitionAuditSnapshot:
        """Persist successful drain without treating it as execution evidence."""
        return self._mark_write_milestone(
            command_id=command_id,
            attempt_number=attempt_number,
            session_id=session_id,
            occurred_at_ms=drained_at_ms,
            expected_outcome=AttemptWriteOutcome.STARTED,
            new_outcome=AttemptWriteOutcome.DRAINED,
            reason="attempt_drained",
            timestamp_column="drain_completed_at_ms",
        )

    # pylint: disable-next=too-many-arguments
    def mark_write_failed(
        self,
        *,
        command_id: str,
        attempt_number: int,
        session_id: str,
        occurred_at_ms: int,
        error: str,
    ) -> TransitionAuditSnapshot:
        """Persist a synchronous, known-not-invoked write failure."""
        return self._mark_write_outcome_and_release(
            command_id=command_id,
            attempt_number=attempt_number,
            session_id=session_id,
            occurred_at_ms=occurred_at_ms,
            error=error,
            expected_outcomes=(AttemptWriteOutcome.PREPARED,),
            new_outcome=AttemptWriteOutcome.FAILED,
            reason=RetryReason.WRITE_FAILED,
        )

    # pylint: disable-next=too-many-arguments
    def mark_write_unknown(
        self,
        *,
        command_id: str,
        attempt_number: int,
        session_id: str,
        occurred_at_ms: int,
        error: str,
    ) -> TransitionAuditSnapshot:
        """Persist uncertainty after writer invocation or drain."""
        return self._mark_write_outcome_and_release(
            command_id=command_id,
            attempt_number=attempt_number,
            session_id=session_id,
            occurred_at_ms=occurred_at_ms,
            error=error,
            expected_outcomes=(
                AttemptWriteOutcome.STARTED,
                AttemptWriteOutcome.DRAINED,
            ),
            new_outcome=AttemptWriteOutcome.UNKNOWN,
            reason=RetryReason.WRITE_UNKNOWN,
        )

    # pylint: disable-next=too-many-arguments
    def _mark_write_outcome_and_release(
        self,
        *,
        command_id: str,
        attempt_number: int,
        session_id: str,
        occurred_at_ms: int,
        error: str,
        expected_outcomes: tuple[AttemptWriteOutcome, ...],
        new_outcome: AttemptWriteOutcome,
        reason: RetryReason,
    ) -> TransitionAuditSnapshot:
        normalized_command_id = _validate_identifier("command_id", command_id)
        _validate_attempt_number(attempt_number)
        normalized_session_id = _validate_identifier("session_id", session_id)
        _validate_sqlite_integer("occurred_at_ms", occurred_at_ms)
        bounded_error = _validate_bounded_text("error", error, 1024)

        def operation(connection: sqlite3.Connection) -> TransitionAuditSnapshot:
            command, attempt = self._require_active_attempt_locked(
                connection,
                command_id=normalized_command_id,
                attempt_number=attempt_number,
                session_id=normalized_session_id,
            )
            if attempt.write_outcome not in expected_outcomes:
                raise StaleAttemptError("attempt write outcome no longer matches")
            placeholders = ",".join("?" for _ in expected_outcomes)
            cursor = connection.execute(
                f"""
                UPDATE command_attempts
                SET write_outcome = ?, write_error = ?
                WHERE command_id = ? AND attempt_number = ? AND session_id = ?
                  AND write_outcome IN ({placeholders})
                """,
                (
                    new_outcome.value,
                    bounded_error,
                    normalized_command_id,
                    attempt_number,
                    normalized_session_id,
                    *(outcome.value for outcome in expected_outcomes),
                ),
            )
            if cursor.rowcount != 1:
                raise StaleAttemptError("attempt write outcome no longer matches")
            return self._release_active_attempt_locked(
                connection,
                command=command,
                attempt_number=attempt_number,
                session_id=normalized_session_id,
                occurred_at_ms=occurred_at_ms,
                reason=reason,
                error=bounded_error,
            )

        return self._run_mutation(reason.value.replace("_", " "), operation)

    # pylint: disable-next=too-many-arguments
    def release_for_retry(
        self,
        *,
        command_id: str,
        attempt_number: int,
        session_id: str,
        occurred_at_ms: int,
        reason: RetryReason,
        error: str | None = None,
    ) -> TransitionAuditSnapshot:
        """Release one exact active attempt, failing at the policy limit."""
        normalized_command_id = _validate_identifier("command_id", command_id)
        _validate_attempt_number(attempt_number)
        normalized_session_id = _validate_identifier("session_id", session_id)
        _validate_sqlite_integer("occurred_at_ms", occurred_at_ms)
        if not isinstance(reason, RetryReason):
            raise ValueError("reason must be a RetryReason")
        if reason in (RetryReason.WRITE_FAILED, RetryReason.WRITE_UNKNOWN):
            raise ValueError(
                "write outcome retry reasons require their dedicated update methods"
            )
        bounded_error = (
            _validate_bounded_text("error", error, 1024)
            if error is not None
            else None
        )

        def operation(connection: sqlite3.Connection) -> TransitionAuditSnapshot:
            command, _attempt = self._require_active_attempt_locked(
                connection,
                command_id=normalized_command_id,
                attempt_number=attempt_number,
                session_id=normalized_session_id,
            )
            return self._release_active_attempt_locked(
                connection,
                command=command,
                attempt_number=attempt_number,
                session_id=normalized_session_id,
                occurred_at_ms=occurred_at_ms,
                reason=reason,
                error=bounded_error,
            )

        return self._run_mutation("release for retry", operation)

    # pylint: disable-next=too-many-arguments
    def _release_active_attempt_locked(
        self,
        connection: sqlite3.Connection,
        *,
        command: TwinCommand,
        attempt_number: int,
        session_id: str,
        occurred_at_ms: int,
        reason: RetryReason,
        error: str | None,
    ) -> TransitionAuditSnapshot:
        next_state = (
            CommandState.FAILED
            if attempt_number >= self._policy.max_attempts
            else CommandState.RETRY_PENDING
        )
        diagnostic = _bounded_message(error or reason.value)
        cursor = connection.execute(
            """
            UPDATE commands
            SET state = ?, updated_at_ms = ?, active_session_id = NULL,
                ack_deadline_ms = NULL, completed_at_ms = ?, last_error = ?
            WHERE command_id = ? AND state = 'awaiting_ack'
              AND attempt_count = ? AND active_session_id = ?
            """,
            (
                next_state.value,
                occurred_at_ms,
                occurred_at_ms if next_state is CommandState.FAILED else None,
                diagnostic,
                command.command_id,
                attempt_number,
                session_id,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleAttemptError(
                "command, attempt number, and session no longer match"
            )
        transition = self._insert_transition_locked(
            connection,
            command_id=command.command_id,
            audit_id=command.audit_id,
            from_state=CommandState.AWAITING_ACK,
            to_state=next_state,
            occurred_at_ms=occurred_at_ms,
            attempt_number=attempt_number,
            session_id=session_id,
            reason=reason.value,
            error_text=error,
        )
        updated_command = self._read_command_locked(connection, command.command_id)
        updated_attempt = self._read_attempt_locked(
            connection, command.command_id, attempt_number
        )
        return TransitionAuditSnapshot(
            updated_command, transition, updated_attempt
        )

    # pylint: disable-next=too-many-arguments
    def _mark_write_milestone(
        self,
        *,
        command_id: str,
        attempt_number: int,
        session_id: str,
        occurred_at_ms: int,
        expected_outcome: AttemptWriteOutcome,
        new_outcome: AttemptWriteOutcome,
        reason: str,
        timestamp_column: str,
    ) -> TransitionAuditSnapshot:
        normalized_command_id = _validate_identifier("command_id", command_id)
        _validate_attempt_number(attempt_number)
        normalized_session_id = _validate_identifier("session_id", session_id)
        _validate_sqlite_integer("occurred_at_ms", occurred_at_ms)

        def operation(connection: sqlite3.Connection) -> TransitionAuditSnapshot:
            command, _attempt = self._require_active_attempt_locked(
                connection,
                command_id=normalized_command_id,
                attempt_number=attempt_number,
                session_id=normalized_session_id,
            )
            cursor = connection.execute(
                f"""
                UPDATE command_attempts
                SET {timestamp_column} = ?, write_outcome = ?
                WHERE command_id = ? AND attempt_number = ? AND session_id = ?
                  AND write_outcome = ?
                """,
                (
                    occurred_at_ms,
                    new_outcome.value,
                    normalized_command_id,
                    attempt_number,
                    normalized_session_id,
                    expected_outcome.value,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleAttemptError("attempt write milestone no longer matches")
            connection.execute(
                "UPDATE commands SET updated_at_ms = ? WHERE command_id = ?",
                (occurred_at_ms, normalized_command_id),
            )
            transition = self._insert_transition_locked(
                connection,
                command_id=normalized_command_id,
                audit_id=command.audit_id,
                from_state=CommandState.AWAITING_ACK,
                to_state=CommandState.AWAITING_ACK,
                occurred_at_ms=occurred_at_ms,
                attempt_number=attempt_number,
                session_id=normalized_session_id,
                reason=reason,
            )
            updated_command = self._read_command_locked(
                connection, normalized_command_id
            )
            updated_attempt = self._read_attempt_locked(
                connection, normalized_command_id, attempt_number
            )
            return TransitionAuditSnapshot(
                updated_command, transition, updated_attempt
            )

        return self._run_mutation(reason.replace("_", " "), operation)

    def _require_active_attempt_locked(
        self,
        connection: sqlite3.Connection,
        *,
        command_id: str,
        attempt_number: int,
        session_id: str,
    ) -> tuple[TwinCommand, CommandAttempt]:
        try:
            command = self._read_command_locked(connection, command_id)
        except StoreRecordNotFound as error:
            raise StaleAttemptError(
                "command, attempt number, and session no longer match"
            ) from error
        if (
            command.state is not CommandState.AWAITING_ACK
            or command.attempt_count != attempt_number
            or command.active_session_id != session_id
        ):
            raise StaleAttemptError(
                "command, attempt number, and session no longer match"
            )
        try:
            attempt = self._read_attempt_locked(
                connection, command_id, attempt_number
            )
        except StoreRecordNotFound as error:
            raise StaleAttemptError(
                "command, attempt number, and session no longer match"
            ) from error
        if attempt.session_id != session_id:
            raise StaleAttemptError(
                "command, attempt number, and session no longer match"
            )
        return command, attempt

    def _read_command_locked(
        self, connection: sqlite3.Connection, command_id: str
    ) -> TwinCommand:
        row = connection.execute(
            "SELECT * FROM commands WHERE command_id = ?", (command_id,)
        ).fetchone()
        if row is None:
            raise StoreRecordNotFound(f"command record not found: {command_id}")
        try:
            return _command_from_row(row)
        except TwinStoreError as error:
            self._set_degradation(str(error))
            raise

    def _read_attempt_locked(
        self,
        connection: sqlite3.Connection,
        command_id: str,
        attempt_number: int,
    ) -> CommandAttempt:
        row = connection.execute(
            """
            SELECT * FROM command_attempts
            WHERE command_id = ? AND attempt_number = ?
            """,
            (command_id, attempt_number),
        ).fetchone()
        if row is None:
            raise StoreRecordNotFound(
                f"attempt record not found: {command_id}/{attempt_number}"
            )
        try:
            return _attempt_from_row(row)
        except TwinStoreError as error:
            self._set_degradation(str(error))
            raise

    @staticmethod
    def _insert_transition_locked(  # pylint: disable=too-many-arguments
        connection: sqlite3.Connection,
        *,
        command_id: str,
        audit_id: str,
        from_state: CommandState | None,
        to_state: CommandState,
        occurred_at_ms: int,
        reason: str,
        attempt_number: int | None = None,
        session_id: str | None = None,
        error_text: str | None = None,
        wire_frame: bytes | None = None,
        evidence_frame: bytes | None = None,
    ) -> CommandTransition:
        cursor = connection.execute(
            """
            INSERT INTO command_transitions(
                command_id, audit_id, from_state, to_state, occurred_at_ms,
                attempt_number, session_id, reason, error_text, wire_frame,
                evidence_frame
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command_id,
                audit_id,
                from_state.value if from_state is not None else None,
                to_state.value,
                occurred_at_ms,
                attempt_number,
                session_id,
                _bounded_message(reason),
                _bounded_message(error_text) if error_text is not None else None,
                wire_frame,
                evidence_frame,
            ),
        )
        transition_id = cursor.lastrowid
        if not isinstance(transition_id, int):
            raise TwinStoreError("transition insert returned no durable ID")
        row = connection.execute(
            """
            SELECT transition_id, command_id, audit_id, from_state, to_state,
                   occurred_at_ms, attempt_number, session_id, reason,
                   error_text, wire_frame, evidence_frame
            FROM command_transitions WHERE transition_id = ?
            """,
            (transition_id,),
        ).fetchone()
        if row is None:
            raise TwinStoreError("inserted transition disappeared")
        return _transition_from_row(row)

    def _run_mutation(self, label: str, operation):
        """Run one fail-closed immediate transaction with BaseException rollback."""
        with self._mutex:
            self.verify_health()
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
            except BaseException as error:  # pylint: disable=broad-exception-caught
                if not isinstance(error, Exception):
                    raise
                reason = _bounded_message(f"failed to begin {label}: {error}")
                self._set_degradation(reason)
                raise TwinStoreError(reason) from error

            committing = False
            try:
                result = operation(connection)
                committing = True
                connection.execute("COMMIT")
                if connection.in_transaction:
                    raise TwinStoreError(f"{label} commit left a live transaction")
                return result
            except BaseException as error:  # pylint: disable=broad-exception-caught
                rollback_error: BaseException | None = None
                transaction_was_live = connection.in_transaction
                try:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                except BaseException as caught:  # pylint: disable=broad-exception-caught
                    rollback_error = caught
                ambiguous_commit = committing and not transaction_was_live
                if rollback_error is not None or ambiguous_commit:
                    reason = f"{label} failed: {error}"
                    if rollback_error is not None:
                        reason = f"{reason}; rollback failed: {rollback_error}"
                    if ambiguous_commit:
                        reason = f"{reason}; durable commit outcome is ambiguous"
                    bounded_reason = _bounded_message(reason)
                    self._set_degradation(bounded_reason)
                    raise TwinStoreError(bounded_reason) from (
                        rollback_error or error
                    )
                if not isinstance(error, Exception):
                    raise
                if isinstance(
                    error,
                    (StaleAttemptError, StoreRecordNotFound, ValueError, TypeError),
                ):
                    raise
                reason = _bounded_message(f"{label} failed: {error}")
                self._set_degradation(reason)
                raise TwinStoreError(reason) from error

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


def _validate_identifier(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if not value or value.strip() != value:
        raise ValueError(f"{name} must be non-empty and normalized")
    if len(value) > 256:
        raise ValueError(f"{name} exceeds 256 characters")
    return value


def _validate_bounded_text(name: str, value: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if not value:
        raise ValueError(f"{name} must be non-empty")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return value


def _validate_attempt_number(attempt_number: int) -> None:
    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or not 1 <= attempt_number <= 8
    ):
        raise ValueError("attempt_number must be between 1 and 8")


def _checked_add_milliseconds(name: str, base: int, delta: int) -> int:
    _validate_sqlite_integer(name, base)
    result = base + delta
    if result > _MAX_SQLITE_INTEGER:
        raise OverflowError(f"{name} exceeds SQLite integer range")
    return result


def _validate_unpersisted_ingress(ingress: ControlIngress) -> ControlIngress:
    if not isinstance(ingress, ControlIngress):
        raise TypeError("ingress must be a ControlIngress")
    if any(
        value is not None
        for value in (
            ingress.disposition,
            ingress.reason,
            ingress.command_id,
            ingress.audit_id,
        )
    ):
        raise ValueError("ingress envelope is already persisted")
    _validate_identifier("ingress_id", ingress.ingress_id)
    _validate_sqlite_integer("received_at_ms", ingress.received_at_ms)
    if not isinstance(ingress.topic, str) or len(ingress.topic) > 1024:
        raise ValueError("topic must be a string of at most 1024 characters")
    if ingress.topic_device_id is not None:
        _validate_device_id(ingress.topic_device_id)
    if type(ingress.retain) is not bool:  # pylint: disable=unidiomatic-typecheck
        raise ValueError("retain must be a boolean")
    if not isinstance(ingress.raw_text, str) or len(ingress.raw_text) > 16384:
        raise ValueError("raw_text must be a string of at most 16384 characters")
    return ingress


def _ensure_ingress_absent(
    connection: sqlite3.Connection, ingress_id: str
) -> None:
    if connection.execute(
        "SELECT 1 FROM control_ingress_audit WHERE ingress_id = ?",
        (ingress_id,),
    ).fetchone() is not None:
        raise ValueError(f"ingress_id is already recorded: {ingress_id}")


def _validate_rendered_attempt(
    rendered: RenderedAttempt, used_ver_texts: tuple[str, ...]
) -> None:
    if not isinstance(rendered, RenderedAttempt):
        raise ValueError("renderer must return RenderedAttempt")
    _validate_attempt_render_fields(
        rendered.tsec_text,
        rendered.ver_text,
        rendered.crc_text,
        rendered.wire_frame,
    )
    if rendered.ver_text in used_ver_texts:
        raise ValueError("ver_text was already used by this command")


def _validate_attempt_render_fields(
    tsec_text: object,
    ver_text: object,
    crc_text: object,
    wire_frame: object,
) -> None:
    if not isinstance(tsec_text, str) or not tsec_text:
        raise ValueError("tsec_text must be non-empty text")
    if (
        not isinstance(ver_text, str)
        or _FIVE_DIGITS.fullmatch(ver_text) is None
        or int(ver_text) > 65535
    ):
        raise ValueError("ver_text must be a five-digit uint16 decimal")
    if (
        not isinstance(crc_text, str)
        or _FIVE_DIGITS.fullmatch(crc_text) is None
        or int(crc_text) > 65535
    ):
        raise ValueError("crc_text must be a five-digit uint16 decimal")
    if type(wire_frame) is not bytes:  # pylint: disable=unidiomatic-typecheck
        raise ValueError("wire_frame must be exact bytes")
    if not wire_frame:
        raise ValueError("wire_frame must be non-empty")
    if len(wire_frame) > _MAX_WIRE_FRAME_BYTES:
        raise ValueError("wire_frame exceeds 1048576 bytes")


def _validate_evidence_frame(evidence_frame: bytes) -> bytes:
    if type(evidence_frame) is not bytes:  # pylint: disable=unidiomatic-typecheck
        raise ValueError("evidence_frame must be exact bytes")
    if len(evidence_frame) > _MAX_WIRE_FRAME_BYTES:
        raise ValueError("evidence_frame exceeds 1048576 bytes")
    return evidence_frame


# pylint: disable-next=too-many-arguments
def _validate_response_inputs(
    *,
    command_id: str,
    attempt_number: int,
    session_id: str,
    response: SettingResponse,
    expected_result: str,
    received_at_ms: int,
    evidence_frame: bytes,
) -> tuple[str, str]:
    normalized_command_id = _validate_identifier("command_id", command_id)
    _validate_attempt_number(attempt_number)
    normalized_session_id = _validate_identifier("session_id", session_id)
    if len(normalized_session_id) > 128:
        raise ValueError("session_id exceeds 128 characters")
    if not isinstance(response, SettingResponse):
        raise TypeError("response must be a SettingResponse")
    if response.result != expected_result:
        raise ValueError(f"response result must be {expected_result}")
    if (
        not isinstance(response.fingerprint, str)
        or _SHA256_HEX.fullmatch(response.fingerprint) is None
    ):
        raise ValueError("response fingerprint must be lowercase SHA-256 hex")
    if response.reason is not None:
        _validate_bounded_text("response reason", response.reason, 1024)
    if response.rdt_text is not None:
        _validate_bounded_text("response Rdt", response.rdt_text, 1024)
    _validate_sqlite_integer("received_at_ms", received_at_ms)
    normalized_frame = _validate_evidence_frame(evidence_frame)
    if hashlib.sha256(normalized_frame).hexdigest() != response.fingerprint:
        raise ValueError("response fingerprint does not match evidence_frame")
    return normalized_command_id, normalized_session_id


def _validate_response_attempt_window(
    attempt: CommandAttempt, received_at_ms: int
) -> None:
    if attempt.write_outcome not in (
        AttemptWriteOutcome.STARTED,
        AttemptWriteOutcome.DRAINED,
    ):
        raise StaleAttemptError("response arrived before the wire write started")
    if attempt.write_started_at_ms is None:
        raise StaleAttemptError("response attempt has no durable write start")
    if received_at_ms < attempt.write_started_at_ms:
        raise StaleAttemptError("response timestamp precedes the wire write start")
    if received_at_ms > attempt.ack_deadline_ms:
        raise StaleAttemptError("response arrived after ACK deadline")


def _validate_evidence_id(evidence_id: str) -> str:
    if (
        not isinstance(evidence_id, str)
        or _SHA256_HEX.fullmatch(evidence_id) is None
    ):
        raise ValueError("evidence_id must be lowercase SHA-256 hex")
    return evidence_id


def _validate_setting_event(evidence: SettingEvent) -> tuple[SettingEvent, bool]:
    if not isinstance(evidence, SettingEvent):
        raise TypeError("evidence must be a SettingEvent")
    _validate_evidence_id(evidence.evidence_id)
    _validate_device_id(evidence.device_id)
    _validate_sqlite_integer("event_id_set", evidence.event_id_set)
    _validate_bounded_text("device_dt", evidence.device_dt, 1024)
    _validate_bounded_text("content_text", evidence.content_text, 16384)
    _validate_bounded_text("table_name", evidence.table_name, 128)
    _validate_bounded_text("item_name", evidence.item_name, 128)
    if not isinstance(evidence.old_value_text, str) or len(evidence.old_value_text) > 1024:
        raise ValueError("old_value_text must be text of at most 1024 characters")
    if not isinstance(evidence.new_value_text, str) or len(evidence.new_value_text) > 1024:
        raise ValueError("new_value_text must be text of at most 1024 characters")
    derived = derive_event_evidence_id(
        evidence.device_id,
        evidence.event_id_set,
        evidence.device_dt,
        evidence.content_text,
    )
    if derived != evidence.evidence_id:
        raise ValueError("evidence_id does not match the immutable event envelope")
    parsed_content = parse_setting_event_content(evidence.content_text)
    content_is_consistent = parsed_content == (
        evidence.table_name,
        evidence.item_name,
        evidence.old_value_text,
        evidence.new_value_text,
    )
    return evidence, content_is_consistent


def _parse_protocol_datetime(value: str | None) -> datetime | None:
    if value is None or not isinstance(value, str):
        return None
    for date_format in ("%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue
    return None


def _device_from_row(row: tuple[Any, ...]) -> DeviceState:
    device_id = _persisted_text("device device_id", row[0])
    return DeviceState(
        device_id,
        _persisted_device_integer("first_seen_at_ms", row[1]),
        _persisted_device_integer("last_seen_at_ms", row[2]),
        _persisted_device_integer("next_wire_id", row[3]),
        _persisted_device_integer("next_wire_id_set", row[4]),
    )


def _persisted_integer(name: str, value: object) -> int:
    if type(value) is not int:  # pylint: disable=unidiomatic-typecheck
        raise TwinStoreError(f"persisted {name} must be an exact SQLite integer")
    if not 0 <= value <= _MAX_SQLITE_INTEGER:
        raise TwinStoreError(f"persisted {name} is outside SQLite integer range")
    return value


def _persisted_optional_integer(name: str, value: object) -> int | None:
    return None if value is None else _persisted_integer(name, value)


def _persisted_text(
    name: str, value: object, *, allow_empty: bool = False
) -> str:
    if type(value) is not str:  # pylint: disable=unidiomatic-typecheck
        raise TwinStoreError(f"persisted {name} must be exact text")
    if not allow_empty and not value:
        raise TwinStoreError(f"persisted {name} must be non-empty")
    return value


def _persisted_optional_text(name: str, value: object) -> str | None:
    return None if value is None else _persisted_text(name, value)


def _persisted_identifier(name: str, value: object, maximum: int) -> str:
    text = _persisted_text(name, value)
    if text.strip() != text:
        raise TwinStoreError(f"persisted {name} must be normalized")
    if len(text) > maximum:
        raise TwinStoreError(f"persisted {name} exceeds {maximum} characters")
    return text


def _persisted_optional_blob(name: str, value: object) -> bytes | None:
    if value is None:
        return None
    return _persisted_blob(name, value)


def _persisted_blob(name: str, value: object) -> bytes:
    if type(value) is not bytes:  # pylint: disable=unidiomatic-typecheck
        raise TwinStoreError(f"persisted {name} must be exact bytes")
    return value


def _command_from_row(row: tuple[Any, ...]) -> TwinCommand:
    try:
        state = CommandState(_persisted_text("command state", row[7]))
    except ValueError as error:
        raise TwinStoreError("persisted command state is invalid") from error
    return TwinCommand(
        command_id=_persisted_text("command command_id", row[0]),
        audit_id=_persisted_text("command audit_id", row[1]),
        device_id=_persisted_text("command device_id", row[2]),
        table_name=_persisted_text("command table_name", row[3]),
        item_name=_persisted_text("command item_name", row[4]),
        value_text=_persisted_text("command value_text", row[5]),
        raw_ingress_text=_persisted_text(
            "command raw_ingress_text", row[6], allow_empty=True
        ),
        state=state,
        created_at_ms=_persisted_integer("command created_at_ms", row[8]),
        updated_at_ms=_persisted_integer("command updated_at_ms", row[9]),
        pending_expires_at_ms=_persisted_integer(
            "command pending_expires_at_ms", row[10]
        ),
        wire_id=_persisted_optional_integer("command wire_id", row[11]),
        wire_id_set=_persisted_optional_integer("command wire_id_set", row[12]),
        wire_dt=_persisted_optional_text("command wire_dt", row[13]),
        attempt_count=_persisted_integer("command attempt_count", row[14]),
        active_session_id=_persisted_optional_text(
            "command active_session_id", row[15]
        ),
        ack_deadline_ms=_persisted_optional_integer(
            "command ack_deadline_ms", row[16]
        ),
        event_deadline_ms=_persisted_optional_integer(
            "command event_deadline_ms", row[17]
        ),
        acked_at_ms=_persisted_optional_integer("command acked_at_ms", row[18]),
        ack_device_rdt=_persisted_optional_text(
            "command ack_device_rdt", row[19]
        ),
        completed_at_ms=_persisted_optional_integer(
            "command completed_at_ms", row[20]
        ),
        predecessor_command_id=_persisted_optional_text(
            "command predecessor_command_id", row[21]
        ),
        last_wire_frame=_persisted_optional_blob(
            "command last_wire_frame", row[22]
        ),
        last_error=_persisted_optional_text("command last_error", row[23]),
    )


def _attempt_from_row(row: tuple[Any, ...]) -> CommandAttempt:
    try:
        outcome = AttemptWriteOutcome(
            _persisted_text("attempt write_outcome", row[12])
        )
    except ValueError as error:
        raise TwinStoreError("persisted attempt write_outcome is invalid") from error
    tsec_text = _persisted_text("attempt tsec_text", row[7], allow_empty=True)
    ver_text = _persisted_text("attempt ver_text", row[8], allow_empty=True)
    crc_text = _persisted_text("attempt crc_text", row[9], allow_empty=True)
    wire_frame = _persisted_blob("attempt wire_frame", row[10])
    try:
        _validate_attempt_render_fields(
            tsec_text, ver_text, crc_text, wire_frame
        )
    except ValueError as error:
        raise TwinStoreError(
            f"persisted attempt render contract is invalid: {error}"
        ) from error
    wire_length = _persisted_integer("attempt wire_length", row[11])
    if wire_length != len(wire_frame):
        raise TwinStoreError("persisted attempt wire length is inconsistent")
    return CommandAttempt(
        command_id=_persisted_identifier("attempt command_id", row[0], 256),
        attempt_number=_persisted_integer("attempt attempt_number", row[1]),
        session_id=_persisted_identifier("attempt session_id", row[2], 128),
        prepared_at_ms=_persisted_integer("attempt prepared_at_ms", row[3]),
        write_started_at_ms=_persisted_optional_integer(
            "attempt write_started_at_ms", row[4]
        ),
        drain_completed_at_ms=_persisted_optional_integer(
            "attempt drain_completed_at_ms", row[5]
        ),
        ack_deadline_ms=_persisted_integer("attempt ack_deadline_ms", row[6]),
        tsec_text=tsec_text,
        ver_text=ver_text,
        crc_text=crc_text,
        wire_frame=wire_frame,
        wire_length=wire_length,
        write_outcome=outcome,
        write_error=_persisted_optional_text("attempt write_error", row[13]),
        response_fingerprint=_persisted_optional_text(
            "attempt response_fingerprint", row[14]
        ),
        response_rdt=_persisted_optional_text("attempt response_rdt", row[15]),
    )


def _transition_from_row(row: tuple[Any, ...]) -> CommandTransition:
    try:
        from_state = (
            CommandState(_persisted_text("transition from_state", row[3]))
            if row[3] is not None
            else None
        )
        to_state = CommandState(
            _persisted_text("transition to_state", row[4])
        )
    except ValueError as error:
        raise TwinStoreError("persisted transition state is invalid") from error
    return CommandTransition(
        transition_id=_persisted_integer("transition transition_id", row[0]),
        command_id=_persisted_text("transition command_id", row[1]),
        audit_id=_persisted_text("transition audit_id", row[2]),
        from_state=from_state,
        to_state=to_state,
        occurred_at_ms=_persisted_integer("transition occurred_at_ms", row[5]),
        attempt_number=_persisted_optional_integer(
            "transition attempt_number", row[6]
        ),
        session_id=_persisted_optional_text("transition session_id", row[7]),
        reason=_persisted_text("transition reason", row[8], allow_empty=True),
        error_text=_persisted_optional_text("transition error_text", row[9]),
        wire_frame=_persisted_optional_blob("transition wire_frame", row[10]),
        evidence_frame=_persisted_optional_blob(
            "transition evidence_frame", row[11]
        ),
    )


def _ingress_from_row(row: tuple[Any, ...]) -> ControlIngress:
    try:
        disposition = IngressDisposition(
            _persisted_text("ingress disposition", row[6])
        )
    except ValueError as error:
        raise TwinStoreError("persisted ingress disposition is invalid") from error
    retain = _persisted_integer("ingress retain", row[4])
    if retain not in (0, 1):
        raise TwinStoreError("persisted ingress retain is invalid")
    return ControlIngress(
        ingress_id=_persisted_text("ingress ingress_id", row[0]),
        received_at_ms=_persisted_integer("ingress received_at_ms", row[1]),
        topic=_persisted_text("ingress topic", row[2], allow_empty=True),
        topic_device_id=_persisted_optional_text(
            "ingress topic_device_id", row[3]
        ),
        retain=bool(retain),
        raw_text=_persisted_text("ingress raw_text", row[5], allow_empty=True),
        disposition=disposition,
        reason=_persisted_text("ingress reason", row[7], allow_empty=True),
        command_id=_persisted_optional_text("ingress command_id", row[8]),
        audit_id=_persisted_optional_text("ingress audit_id", row[9]),
    )


def _event_receipt_from_row(row: tuple[Any, ...]) -> SettingEventReceipt:
    return SettingEventReceipt(
        evidence_id=_persisted_text("event evidence_id", row[0]),
        received_at_ms=_persisted_integer("event received_at_ms", row[1]),
        device_id=_persisted_text("event device_id", row[2]),
        event_id_set=_persisted_integer("event event_id_set", row[3]),
        device_dt=_persisted_text("event device_dt", row[4]),
        table_name=_persisted_text("event table_name", row[5]),
        item_name=_persisted_text("event item_name", row[6]),
        old_value_text=_persisted_text(
            "event old_value_text", row[7], allow_empty=True
        ),
        new_value_text=_persisted_text(
            "event new_value_text", row[8], allow_empty=True
        ),
        evidence_frame=_persisted_blob("event evidence_frame", row[9]),
        disposition=_persisted_text("event disposition", row[10]),
        command_id=_persisted_optional_text("event command_id", row[11]),
        duplicate_count=_persisted_integer("event duplicate_count", row[12]),
        last_seen_at_ms=_persisted_integer("event last_seen_at_ms", row[13]),
    )
