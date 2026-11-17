"""Atomic, device-scoped storage for historical BOX observations only."""

from __future__ import annotations

# JSON validation requires exact types: bool must not pass as an integer.
# pylint: disable=unidiomatic-typecheck

from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4

from settings_constraints import PHASE_CALIBRATION_CONTROLS, PowerControlLimits, validate_setting_value

logger = logging.getLogger(__name__)
OBSERVATION_PROFILE = "oig-confirmed-observations-v1"
ORIGINS = frozenset({"direct", "confirmed_event", "actual_mirror"})
EXCLUDED_TABLES = frozenset({"tbl_events", "tbl_actions", "tbl_device"})
_PHASE_TABLE = "tbl_invertor_prm1"
_PHASE_EVIDENCE = frozenset({"setting_event", "ack_and_table"})
_PHASE_ENCODING = PowerControlLimits(phase_calibration_enabled=True)


def observation_table(table: str) -> bool:
    """Limit durable state to BOX value tables, excluding administrative records."""
    return table.startswith("tbl_") and table not in EXCLUDED_TABLES


def finite_json(value: Any) -> bool:
    """Accept only native finite JSON values, including nested observation values."""
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(finite_json(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and finite_json(item) for key, item in value.items())
    return False


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(value, "%d.%m.%Y %H:%M:%S")
        except ValueError:
            return None


def valid_timestamp(value: Any) -> bool:
    """Validate original device timestamps without assigning a timezone or freshness."""
    return _parse_timestamp(value) is not None


class ConfirmedStateStore:
    """Own a validated cache; replay never implies a fresh or online device."""

    def __init__(self, path: Path | None, device_id: str, namespace: str = "oig_local") -> None:
        self.path = Path(path) if path is not None else None
        self.device_id = device_id
        self.namespace = namespace
        self._dirty = False
        self._writable = True
        self._document: dict[str, Any] = {
            "version": 1,
            "device_id": device_id,
            "namespace": namespace,
            "firmware": None,
            "profile": OBSERVATION_PROFILE,
            "ordering_epoch": 0,
            "reset_reason": None,
            "tables": {},
        }
        self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if not self._valid_document(document):
                raise ValueError("Invalid confirmed observation cache")
        except FileNotFoundError:
            return
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            logger.warning("Confirmed observation cache rejected: %s: %s", self.path, exc)
            # Preserve original bytes before allowing a replacement, even after a bad schema.
            try:
                self.path.replace(self.path.with_name(f"{self.path.name}.rejected-{uuid4().hex}"))
            except OSError:
                self._writable = False
                logger.exception("Cannot preserve rejected confirmed cache; writes disabled")
            return
        self._document = document
        if document["profile"] != OBSERVATION_PROFILE:
            self.set_context(profile=OBSERVATION_PROFILE)

    def _valid_document(self, document: Any) -> bool:
        if not isinstance(document, dict) or not finite_json(document):
            return False
        schema = {
            "version": int,
            "device_id": str,
            "namespace": str,
            "profile": str,
            "ordering_epoch": int,
            "tables": dict,
        }
        if (
            any(type(document.get(key)) is not expected for key, expected in schema.items())
            or document["version"] != 1
            or document["device_id"] != self.device_id
            or document["namespace"] != self.namespace
            or document["ordering_epoch"] < 0
        ):
            return False
        if not {"firmware", "reset_reason"}.issubset(document) or any(
            document.get(key) is not None and type(document[key]) is not str
            for key in ("firmware", "reset_reason")
        ):
            return False
        for table, record in document["tables"].items():
            if (
                not observation_table(table)
                or not isinstance(record, dict)
                or type(record.get("baseline")) is not bool
                or not isinstance(record.get("fields"), dict)
            ):
                return False
            if any(
                not key
                or key.startswith("_")
                or not self._valid_field(field, document["ordering_epoch"])
                for key, field in record["fields"].items()
            ):
                return False
        phase_writes = document.get("phase_calibration_writes", {})
        return isinstance(phase_writes, dict) and all(
            self._valid_phase_write(key, field, document["ordering_epoch"])
            for key, field in phase_writes.items()
        )

    @staticmethod
    def _valid_phase_write(key: str, field: Any, epoch: int) -> bool:
        if (_PHASE_TABLE, key) not in PHASE_CALIBRATION_CONTROLS or not isinstance(field, dict):
            return False
        source = _parse_timestamp(field.get("sourceDT"))
        received = _parse_timestamp(field.get("confirmed_at"))
        return (
            type(field.get("value")) is int
            and validate_setting_value(_PHASE_TABLE, key, field["value"], power_limits=_PHASE_ENCODING)[0]
            and type(field.get("evidence")) is str and field["evidence"] in _PHASE_EVIDENCE
            and type(field.get("ordering_epoch")) is int and 0 <= field["ordering_epoch"] <= epoch
            and source is not None and source.year > 1970
            and received is not None and received.tzinfo is not None
        )

    @staticmethod
    def _valid_field(field: Any, epoch: int) -> bool:
        required = {
            "value",
            "sourceDT",
            "priority",
            "received_at",
            "origin",
            "ordering_epoch",
            "ordering_sourceDT",
            "ordering_priority",
        }
        if not isinstance(field, dict) or not required.issubset(field):
            return False
        schema = {
            "priority": int,
            "ordering_priority": int,
            "ordering_epoch": int,
            "origin": str,
            "received_at": str,
        }
        if any(type(field[key]) is not expected for key, expected in schema.items()):
            return False
        if (
            field["origin"] not in ORIGINS
            or field["priority"] not in (0, 1)
            or field["ordering_priority"] not in (0, 1)
            or not 0 <= field["ordering_epoch"] <= epoch
        ):
            return False
        return valid_timestamp(field["received_at"]) and all(
            field[key] is None or valid_timestamp(field[key])
            for key in ("sourceDT", "ordering_sourceDT")
        )

    def snapshot(self) -> dict[str, Any]:
        """Return isolated provenance and values; callers cannot mutate cache state."""
        return deepcopy(self._document)

    def phase_calibration_writes(self) -> dict[str, Any]:
        """Expose only confirmed values and their provenance to MQTT."""
        return {
            key: {name: field[name] for name in ("value", "confirmed_at", "evidence")}
            for key, field in self._document.get("phase_calibration_writes", {}).items()
        }

    def record_phase_calibration_write(self, key: str, value: int, *, source_dt: str, evidence: str) -> bool:
        """Record an explicit BOX-confirmed write independently of effective telemetry."""
        epoch = self._document["ordering_epoch"]
        field = {"value": value, "sourceDT": source_dt, "evidence": evidence,
                 "confirmed_at": datetime.now(timezone.utc).isoformat(), "ordering_epoch": epoch}
        if not self._valid_phase_write(key, field, epoch):
            return False
        writes = self._document.setdefault("phase_calibration_writes", {})
        previous = writes.get(key)
        if previous is not None and previous["ordering_epoch"] == epoch:
            stamp = _parse_timestamp(source_dt)
            old_stamp = _parse_timestamp(previous["sourceDT"])
            if stamp is None or old_stamp is None or (stamp.tzinfo is None) != (old_stamp.tzinfo is None) or stamp <= old_stamp:
                return False
        writes[key] = field
        self._dirty = True
        self.flush()
        return True

    def record(  # pylint: disable=too-many-arguments
        self,
        table: str,
        fields: dict[str, Any],
        *,
        source_dt: str | None,
        priority: int,
        origin: str,
        baseline: bool = False,
    ) -> bool:
        """Persist accepted observations, independently of broker delivery success."""
        if not observation_table(table) or not fields or not finite_json(fields):
            return False
        if any(not key or key.startswith("_") for key in fields):
            return False
        if type(priority) is not int or priority not in (0, 1) or origin not in ORIGINS:
            return False
        if source_dt is not None and not valid_timestamp(source_dt):
            return False
        if type(baseline) is not bool or baseline and origin != "direct":
            return False
        received_at = datetime.now(timezone.utc).isoformat()
        record = self._document["tables"].setdefault(table, {"baseline": False, "fields": {}})
        record["baseline"] = record["baseline"] or baseline
        for key, value in fields.items():
            previous = record["fields"].get(key, {})
            same_epoch = previous.get("ordering_epoch") == self._document["ordering_epoch"]
            ordering_dt = (
                source_dt
                if source_dt is not None
                else previous.get("ordering_sourceDT") if same_epoch else None
            )
            ordering_priority = (
                priority
                if source_dt is not None
                else previous.get("ordering_priority", 0) if same_epoch else 0
            )
            record["fields"][key] = {
                "value": deepcopy(value),
                "sourceDT": source_dt,
                "priority": priority,
                "received_at": received_at,
                "origin": origin,
                "ordering_epoch": self._document["ordering_epoch"],
                "ordering_sourceDT": ordering_dt,
                "ordering_priority": ordering_priority,
            }
        self._dirty = True
        return self.flush()

    def reset_ordering_epoch(self, reason: str) -> None:
        """Invalidate barriers while preserving original value timestamps/provenance."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("An explicit ordering reset reason is required")
        self._document["ordering_epoch"] += 1
        self._document["reset_reason"] = reason
        self._dirty = True
        self.flush()

    def set_context(self, *, firmware: str | None = None, profile: str | None = None) -> bool:
        """Record observed firmware; changed known context invalidates table baselines."""
        changed = False
        for name, value in (("firmware", firmware), ("profile", profile)):
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Observation context must be a nonempty string")
            old = self._document[name]
            if old == value:
                continue
            self._document[name] = value
            self._dirty = True
            changed = changed or old is not None
        if changed:
            for table in self._document["tables"].values():
                table["baseline"] = False
            self.reset_ordering_epoch("Observed firmware/profile changed")
        else:
            self.flush()
        return changed

    def flush(self) -> bool:
        """Replace a fully written finite JSON file atomically; keep failed writes dirty."""
        if self.path is None:
            self._dirty = False
            return True
        if not self._writable:
            return False
        if not self._dirty:
            return True
        temporary: str | None = None
        try:
            payload = json.dumps(self._document, ensure_ascii=False, allow_nan=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as target:
                temporary = target.name
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, ValueError, RecursionError):
            logger.exception("Failed to persist confirmed observations: %s", self.path)
            return False
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        self._dirty = False
        return True
