"""Frame processor for OIG Proxy v2."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sensor.confirmed_store import ConfirmedStateStore, finite_json, observation_table
from sensor.loader import SensorMapLoader
from sensor.warnings import warning_state
from mqtt.client import MQTTClient
from protocol.parser import parse_mode_start

logger = logging.getLogger(__name__)

ISNEW_TABLES = {"IsNewFW", "IsNewSet", "IsNewWeather"}
_EXPORT_LIMIT_TABLE = "tbl_invertor_prm1"
_EXPORT_LIMIT_KEY = "P_MAX_FEED_GRID"
_MAX_EXPORT_LIMIT_WATTS = (1 << 32) - 1  # Canonical field encoding, independent of write policy.
_BOX_MODE_TABLE = "tbl_box_prms"
# NOTE: kept in sync with proxy/server.py:TRANSPORT_METADATA_KEYS. Not extracted to
# a shared module: the HAOS git-addon rebuild runs `git clean`, which deletes any
# new untracked file before the image is built.
TRANSPORT_METADATA_KEYS = frozenset(
    {
        "Confirm",
        "ID",
        "ID_Server",
        "NewValue",
        "Rdt",
        "Result",
        "TSec",
        "TblItem",
        "Tmr",
        "ToDo",
        "mytimediff",
    }
)


def _positive_integer_watts(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 < value <= _MAX_EXPORT_LIMIT_WATTS:
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


class FrameProcessor:
    """Processes parsed frame data and publishes to MQTT with sensor_map metadata."""

    def __init__(
        self,
        mqtt: MQTTClient,
        sensor_loader: SensorMapLoader,
        proxy_device_id: str = "oig_proxy",
        state_store: ConfirmedStateStore | None = None,
        *,
        maxfg_as_export_limit_readback: bool = False,
    ) -> None:
        """Initialize processor with MQTT client and sensor loader.

        Args:
            mqtt: MQTT client for publishing.
            sensor_loader: Sensor map loader for metadata lookup.
            maxfg_as_export_limit_readback: Opt in to the installation's MAXFG readback alias.
        """
        self._mqtt = mqtt
        self._sensor_loader = sensor_loader
        self._proxy_device_id = proxy_device_id
        self._missing_map_logged: set[str] = set()
        self._actual_mirror_targets = self._build_actual_mirror_targets()
        self._battery_bank_count_by_device: dict[str, int] = {}
        self._last_table_values: dict[tuple[str, str], dict[str, Any]] = {}
        self._field_device_times: dict[tuple[str, str, str], tuple[datetime, int]] = {}
        self._state_store = state_store
        self._table_baselines: set[tuple[str, str]] = set()
        self._maxfg_as_export_limit_readback = maxfg_as_export_limit_readback
        self._direct_export_limits: dict[str, datetime] = {}
        self._export_limit_firmware: dict[str, str] = {}
        self._restore_confirmed_states()

    def _restore_confirmed_states(self) -> None:
        if self._state_store is None:
            return
        snapshot = self._state_store.snapshot()
        device = self._state_store.device_id
        for table, record in snapshot["tables"].items():
            values = {
                key: field["value"] for key, field in record["fields"].items()
            }
            for key, field in record["fields"].items():
                metadata = self._sensor_loader.lookup(table, key)
                if isinstance(metadata, dict):
                    values.update(warning_state(key, field["value"], metadata.get("warnings_3f")))
            self._last_table_values[(device, table)] = values
            if record["baseline"]:
                self._table_baselines.add((device, table))
            for key, field in record["fields"].items():
                stamp = self._parse_device_time(field["ordering_sourceDT"])
                if stamp is not None and field["ordering_epoch"] == snapshot["ordering_epoch"]:
                    self._field_device_times[(device, table, key)] = (
                        stamp,
                        field["ordering_priority"],
                    )
                    if (
                        table == _EXPORT_LIMIT_TABLE
                        and key == _EXPORT_LIMIT_KEY
                        and record["baseline"]
                        and field["origin"] == "direct"
                        and field["sourceDT"] == field["ordering_sourceDT"]
                    ):
                        self._record_direct_export_limit(device, field["value"], stamp)
        banks = self._last_table_values.get((device, "tbl_batt_prms"), {}).get("BAT_N")
        if isinstance(banks, (int, float)) and not isinstance(banks, bool):
            self._battery_bank_count_by_device[device] = int(banks)

    def replay_confirmed_states(self) -> bool:
        """Republish historical BOX values without availability, commands or freshness."""
        if self._state_store is None:
            return True
        complete = True
        device = self._state_store.device_id
        snapshot = self._state_store.snapshot()
        for (source, table), values in self._last_table_values.items():
            if (
                source != device
                or not observation_table(table)
                or not self._table_enabled_for_device(source, table)
            ):
                continue
            if (source, table) not in self._table_baselines:
                if table != _BOX_MODE_TABLE:
                    continue
                mode = snapshot["tables"][table]["fields"].get("MODE")
                if (
                    not mode or mode["origin"] != "confirmed_event"
                    or mode["ordering_epoch"] != snapshot["ordering_epoch"]
                    or not self._is_dated_mode(mode["value"], self._parse_device_time(mode["sourceDT"]))
                ):
                    continue
                values = {"MODE": mode["value"]}
            if values and not self._mqtt.publish_confirmed_state(source, table, dict(values)):
                complete = False
        return complete

    def reset_ordering_epoch(self, device_id: str, reason: str) -> None:
        """Clear ordering barriers only after an explicit device-clock reset decision."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("An explicit ordering reset reason is required")
        self._field_device_times = {
            identity: value
            for identity, value in self._field_device_times.items()
            if identity[0] != device_id
        }
        self._direct_export_limits.pop(device_id, None)
        if self._state_store is not None and self._state_store.device_id == device_id:
            self._state_store.reset_ordering_epoch(reason)

    def _remember_observation(  # pylint: disable=too-many-arguments
        self,
        device: str,
        table: str,
        values: dict[str, Any],
        *,
        source_dt: str | None,
        priority: int,
        origin: str,
    ) -> dict[str, Any]:
        merged = {**self._last_table_values.get((device, table), {}), **values}
        self._last_table_values[(device, table)] = merged
        if (
            self._state_store is not None
            and self._state_store.device_id == device
            and observation_table(table)
        ):
            baseline = origin == "direct"
            if baseline:
                self._table_baselines.add((device, table))
            if not self._state_store.record(
                table,
                values,
                source_dt=source_dt,
                priority=priority,
                origin=origin,
                baseline=baseline,
            ):
                logger.warning("Confirmed observations remain in memory; cache persistence failed")
        return merged

    def _firmware_observation_current(
        self, device: str, stamp: datetime | None, priority: int
    ) -> bool:
        if stamp is None:
            return True
        for (source, _table, key), (previous, old_priority) in self._field_device_times.items():
            if source != device or key not in {"Fw", "SW"}:
                continue
            if (stamp.tzinfo is None) != (previous.tzinfo is None):
                continue
            if stamp < previous or stamp == previous and priority < old_priority:
                return False
        return True

    def _export_limit_alias_enabled(self, device_id: str) -> bool:
        return (
            self._maxfg_as_export_limit_readback
            and bool(device_id)
            and device_id != "unknown"
            and (self._state_store is None or self._state_store.device_id == device_id)
        )

    def _record_direct_export_limit(
        self, device_id: str, value: object, stamp: datetime | None,
    ) -> None:
        if not self._export_limit_alias_enabled(device_id):
            return
        if stamp is None or stamp.year <= 1970 or _positive_integer_watts(value) is None:
            self._direct_export_limits.pop(device_id, None)
        else:
            self._direct_export_limits[device_id] = stamp

    def _export_limit_readback(
        self, device_id: str, value: object, stamp: datetime | None,
    ) -> int | None:
        baseline = self._direct_export_limits.get(device_id)
        watts = _positive_integer_watts(value)
        if stamp is None or baseline is None or watts is None:
            return None
        if (stamp.tzinfo is None) != (baseline.tzinfo is None) or stamp <= baseline:
            return None
        previous = self._field_device_times.get((device_id, _EXPORT_LIMIT_TABLE, _EXPORT_LIMIT_KEY))
        if previous is not None:
            previous_stamp, _priority = previous
            if (stamp.tzinfo is None) != (previous_stamp.tzinfo is None) or stamp <= previous_stamp:
                return None
        if self._sensor_loader.lookup(_EXPORT_LIMIT_TABLE, _EXPORT_LIMIT_KEY) is None:
            return None
        if self._accept_field_time(device_id, _EXPORT_LIMIT_TABLE, _EXPORT_LIMIT_KEY, stamp, 0):
            return watts
        return None

    def _observe_firmware(
        self, device_id: str, firmware: str,
    ) -> None:
        if self._state_store is None:
            previous = self._export_limit_firmware.get(device_id)
            self._export_limit_firmware[device_id] = firmware
            if previous is not None and previous != firmware:
                self._direct_export_limits.pop(device_id, None)
        elif self._state_store.set_context(firmware=firmware):
            self._direct_export_limits.pop(device_id, None)
            self._field_device_times = {
                identity: value
                for identity, value in self._field_device_times.items()
                if identity[0] != device_id
            }
            self._table_baselines = {
                identity for identity in self._table_baselines if identity[0] != device_id
            }

    def _can_publish_observation(self, device: str, table: str) -> bool:
        return (
            self._state_store is None
            or self._state_store.device_id != device
            or not observation_table(table)
            or (device, table) in self._table_baselines
        )

    def _build_actual_mirror_targets(self) -> dict[str, str]:
        priority = [
            "tbl_dc_in",
            "tbl_ac_in",
            "tbl_ac_out",
            "tbl_batt",
            "tbl_batt_prms",
            "tbl_box",
            "tbl_box_prms",
            "tbl_invertor_prms",
            "tbl_invertor_prm1",
            "tbl_invertor_prm2",
            "tbl_boiler",
            "tbl_boiler_prms",
            "tbl_h_pump_prms",
            "tbl_aircon_prms",
            "tbl_wl_charge_prms",
            "tbl_recuper_prms",
        ]
        by_key: dict[str, set[str]] = {}
        for table, key, _metadata in self._sensor_loader.iter_sensors():
            if table in ("tbl_actual", "IsNewFW", "IsNewSet", "IsNewWeather"):
                continue
            if not table.startswith("tbl_"):
                continue
            by_key.setdefault(key, set()).add(table)

        out: dict[str, str] = {}
        for key, tables in by_key.items():
            if not tables:
                continue
            chosen = None
            for table in priority:
                if table in tables:
                    chosen = table
                    break
            if chosen is None:
                continue
            out[key] = chosen
        return out

    def _battery_bank_count(self, device_id: str) -> int:
        return self._battery_bank_count_by_device.get(device_id, 1)

    def _table_enabled_for_device(self, device_id: str, table: str) -> bool:
        if table == "tbl_batt_prm2" and self._battery_bank_count(device_id) < 2:
            return False
        return True

    def _target_device_id(self, source_device_id: str, table: str) -> str:
        if table in ("tbl_events", "proxy_status", "twin_state"):
            return self._proxy_device_id
        return source_device_id

    def _publish_metadata_discovery(
        self, device_id: str, table: str, key: str, metadata: dict[str, Any]
    ) -> bool:
        return bool(
            self._mqtt.send_discovery(
                device_id=device_id,
                table=table,
                sensor_key=key,
                sensor_name=metadata.get("name_cs") or metadata.get("name") or key,
                unit=metadata.get("unit_of_measurement") or "",
                device_class=metadata.get("device_class") or "",
                state_class=metadata.get("state_class") or "",
                icon=metadata.get("icon") or "",
                device_mapping=metadata.get("device_mapping") or "",
                entity_category=metadata.get("entity_category") or "",
                is_binary=bool(metadata.get("is_binary", False)),
                enum_map=metadata.get("enum_map") or None,
            )
        )

    def publish_all_discovery(self, device_id: str) -> bool:
        """Publish configured device metadata and released command discovery."""
        if not device_id or device_id == "unknown":
            return False
        complete = True
        for table, key, metadata in self._sensor_loader.iter_sensors():
            if table in ISNEW_TABLES or table == "proxy_control":
                continue
            if not self._table_enabled_for_device(device_id, table):
                continue
            if not self._publish_metadata_discovery(
                self._target_device_id(device_id, table), table, key, metadata
            ):
                complete = False
        return complete

    @staticmethod
    def _is_dated_mode(value: Any, stamp: datetime | None) -> bool:
        return (
            isinstance(value, int) and not isinstance(value, bool)
            and value in range(6) and stamp is not None and stamp.year > 1970
        )

    @staticmethod
    def _parse_device_time(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        value = value.strip()
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.strptime(value, "%d.%m.%Y %H:%M:%S")
            except ValueError:
                return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else parsed

    def _accept_field_time(
        self, device_id: str, table: str, key: str, stamp: datetime | None, priority: int
    ) -> bool:
        if stamp is None:
            return True  # An untimed observation is visible but cannot advance ordering proof.
        identity = (device_id, table, key)
        previous = self._field_device_times.get(identity)
        if previous is not None:
            previous_stamp, previous_priority = previous
            if (stamp.tzinfo is None) != (previous_stamp.tzinfo is None):
                logger.warning(
                    "Device timestamp basis changed for %s:%s:%s; ordering is unproven",
                    device_id,
                    table,
                    key,
                )
            elif stamp < previous_stamp or (
                stamp == previous_stamp and priority < previous_priority
            ):
                logger.warning(
                    "Older BOX observation ignored for %s:%s:%s (DT=%s, current=%s)",
                    device_id,
                    table,
                    key,
                    stamp,
                    previous_stamp,
                )
                return False
        self._field_device_times[identity] = (stamp, priority)
        return True

    async def process(
        self, device_id: str, table: str, data: dict[str, Any], *, confirmed_event: bool = False
    ) -> None:
        """Process frame data and publish to MQTT.

        For each key in data (not prefixed with '_'):
        - Look up sensor metadata from sensor_map
        - Send HA discovery config
        - Decode warnings if field has warnings_3f definition
        - Publish state data

        Args:
            device_id: Device identifier.
            table: Table name (e.g., "tbl_actual").
            data: Parsed frame data dictionary.
        """
        if not data:
            return

        native_actual = table == "tbl_actual"
        if table in ISNEW_TABLES:
            table = "tbl_actual"

        stamp = self._parse_device_time(data.get("_dt"))
        if table == "tbl_events" and stamp is not None and stamp.year > 1970:
            mode = parse_mode_start(data.get("Type"), data.get("Content"))
            if mode is not None:
                await self.process(
                    device_id, _BOX_MODE_TABLE, {"MODE": mode, "_dt": data["_dt"]},
                    confirmed_event=True,
                )
        priority = 1 if confirmed_event else 0
        if observation_table(table) and (
            self._export_limit_alias_enabled(device_id)
            or (self._state_store is not None and self._state_store.device_id == device_id)
        ):
            # Firmware observations are context, never proof of live control readiness.
            for firmware_key in ("Fw", "SW"):
                firmware = data.get(firmware_key)
                if (
                    isinstance(firmware, str)
                    and firmware.strip()
                    and self._firmware_observation_current(device_id, stamp, priority)
                ):
                    self._observe_firmware(device_id, firmware)

        if table == "tbl_batt_prms":
            bat_n = data.get("BAT_N")
            if self._state_store is None and isinstance(bat_n, (int, float)) and finite_json(bat_n):
                self._battery_bank_count_by_device[device_id] = int(bat_n)

        if not self._table_enabled_for_device(device_id, table):
            logger.debug(
                "Skipping %s for %s (battery banks=%d)",
                table,
                device_id,
                self._battery_bank_count(device_id),
            )
            return

        target_device_id = self._target_device_id(device_id, table)
        pub_data: dict[str, Any] = {}

        if table == "tbl_batt_prm2":
            prm1_values = self._last_table_values.get((device_id, "tbl_batt_prm1"), {})
            deduped: dict[str, Any] = {}
            for key, value in data.items():
                if key.startswith("_"):
                    continue
                if key in prm1_values and prm1_values[key] == value:
                    continue
                deduped[key] = value
            if not deduped:
                logger.debug(
                    "Skipping tbl_batt_prm2 for %s (all values equal to tbl_batt_prm1)",
                    device_id,
                )
                return
            # Preserve frame provenance for ordering and durable observations.
            data = {**deduped, **{key: value for key, value in data.items() if key.startswith("_")}}

        transport_metadata_frame = self._is_transport_metadata_frame(data)
        if stamp is None:
            logger.debug(
                "BOX observation for %s:%s has no usable device DT; temporal ordering unproven",
                device_id,
                table,
            )
        for key, value in data.items():
            # Skip internal keys prefixed with _
            if key.startswith("_"):
                continue

            if transport_metadata_frame and key in TRANSPORT_METADATA_KEYS:
                continue
            if self._state_store is not None and not finite_json(value):
                logger.warning(
                    "Non-JSON/nonfinite observation rejected for %s:%s:%s", device_id, table, key
                )
                continue
            if not self._accept_field_time(device_id, table, key, stamp, priority):
                continue

            # Look up sensor metadata
            metadata = self._sensor_loader.lookup(table, key)

            if metadata is None:
                miss_key = f"{table}:{key}"
                if miss_key not in self._missing_map_logged:
                    logger.warning("Missing sensor_map entry for %s", miss_key)
                    self._missing_map_logged.add(miss_key)
                # Still publish the raw value even without metadata
                pub_data[key] = value
                continue

            # Extract metadata fields with defaults
            sensor_name = metadata.get("name_cs") or metadata.get("name") or key
            unit = metadata.get("unit_of_measurement") or ""
            device_class = metadata.get("device_class") or ""
            state_class = metadata.get("state_class") or ""
            is_binary = metadata.get("is_binary", False)
            entity_category = metadata.get("entity_category") or ""
            device_mapping = metadata.get("device_mapping") or ""
            enum_map = metadata.get("enum_map") or None

            # Send HA discovery
            self._mqtt.send_discovery(
                device_id=target_device_id,
                table=table,
                sensor_key=key,
                sensor_name=sensor_name,
                unit=unit,
                device_class=device_class,
                state_class=state_class,
                device_mapping=device_mapping,
                entity_category=entity_category,
                is_binary=is_binary,
                enum_map=enum_map,
            )

            if device_class == "timestamp" and isinstance(value, str):
                try:
                    dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    pub_data[key] = dt.isoformat()
                except ValueError:
                    pub_data[key] = value
            else:
                pub_data[key] = value

            pub_data.update(warning_state(key, value, metadata.get("warnings_3f")))

        # Publish state if we have any data
        if pub_data:
            if table == _EXPORT_LIMIT_TABLE and _EXPORT_LIMIT_KEY in pub_data and not transport_metadata_frame:
                if not confirmed_event:
                    self._record_direct_export_limit(device_id, pub_data[_EXPORT_LIMIT_KEY], stamp)
                elif stamp is None:
                    self._direct_export_limits.pop(device_id, None)
            if table == "tbl_batt_prms" and self._state_store is not None:
                accepted_banks = pub_data.get("BAT_N")
                if isinstance(accepted_banks, (int, float)) and not isinstance(
                    accepted_banks, bool
                ):
                    self._battery_bank_count_by_device[device_id] = int(accepted_banks)
            source_dt = data.get("_dt") if stamp is not None else None
            origin = "confirmed_event" if confirmed_event else "direct"
            merged = self._remember_observation(
                device_id, table, pub_data, source_dt=source_dt, priority=priority, origin=origin
            )
            if self._can_publish_observation(device_id, table):
                self._mqtt.publish_state(target_device_id, table, merged)
            elif table == _BOX_MODE_TABLE and confirmed_event and self._is_dated_mode(pub_data.get("MODE"), stamp):
                self._mqtt.publish_state(target_device_id, table, {"MODE": pub_data["MODE"]})
            logger.debug("Published %d keys for %s:%s", len(merged), target_device_id, table)

            if table == "tbl_actual":
                mirror_payloads: dict[str, dict[str, Any]] = {}
                for key, value in pub_data.items():
                    if (
                        key.endswith("_warnings")
                        or key.endswith("_warnings_cs")
                        or key.endswith("_warning_codes")
                        or key.endswith("_status_cs")
                    ):
                        continue
                    mirror_table = self._actual_mirror_targets.get(key)
                    if not mirror_table:
                        continue
                    mirror_metadata = self._sensor_loader.lookup(mirror_table, key)
                    if mirror_metadata is None or not self._accept_field_time(device_id, mirror_table, key, stamp, priority):
                        continue
                    mirrored = mirror_payloads.setdefault(mirror_table, {})
                    mirrored[key] = value
                    mirrored.update(warning_state(key, value, mirror_metadata.get("warnings_3f")))

                if (
                    native_actual
                    and not confirmed_event
                    and not transport_metadata_frame
                    and _EXPORT_LIMIT_KEY not in mirror_payloads.get(_EXPORT_LIMIT_TABLE, {})
                ):
                    export_limit = self._export_limit_readback(device_id, pub_data.get("MAXFG"), stamp)
                    if export_limit is not None:
                        mirror_payloads.setdefault(_EXPORT_LIMIT_TABLE, {})[_EXPORT_LIMIT_KEY] = export_limit

                for mirror_table, mirror_data in mirror_payloads.items():
                    merged_mirror = self._remember_observation(
                        device_id,
                        mirror_table,
                        mirror_data,
                        source_dt=source_dt,
                        priority=priority,
                        origin="actual_mirror",
                    )
                    if self._can_publish_observation(device_id, mirror_table):
                        self._mqtt.publish_state(target_device_id, mirror_table, merged_mirror)
                    logger.debug(
                        "Mirrored %d keys from tbl_actual to %s for %s",
                        len(merged_mirror),
                        mirror_table,
                        target_device_id,
                    )

    @staticmethod
    def _is_transport_metadata_frame(data: dict[str, Any]) -> bool:
        result = str(data.get("Result") or "")
        if result in {"ACK", "END", "IsNewFW", "IsNewSet", "IsNewWeather"}:
            return True

        keys = {key for key in data if not key.startswith("_")}
        if {"TblItem", "NewValue"}.issubset(keys) and keys & {
            "Confirm",
            "ID",
            "ID_Server",
            "TSec",
            "mytimediff",
        }:
            return True

        return False
