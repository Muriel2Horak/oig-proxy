"""Frame processor for OIG Proxy v2."""

from __future__ import annotations

import logging
from typing import Any

from sensor.loader import SensorMapLoader
from sensor.warnings import decode_warning_details, decode_warnings
from mqtt.client import MQTTClient

logger = logging.getLogger(__name__)

ISNEW_TABLES = {"IsNewFW", "IsNewSet", "IsNewWeather"}


class FrameProcessor:
    """Processes parsed frame data and publishes to MQTT with sensor_map metadata."""

    def __init__(
        self,
        mqtt: MQTTClient,
        sensor_loader: SensorMapLoader,
        proxy_device_id: str = "oig_proxy",
    ) -> None:
        """Initialize processor with MQTT client and sensor loader.

        Args:
            mqtt: MQTT client for publishing.
            sensor_loader: Sensor map loader for metadata lookup.
        """
        self._mqtt = mqtt
        self._sensor_loader = sensor_loader
        self._proxy_device_id = proxy_device_id
        self._missing_map_logged: set[str] = set()
        self._actual_mirror_targets = self._build_actual_mirror_targets()
        self._battery_bank_count_by_device: dict[str, int] = {}
        self._last_table_values: dict[tuple[str, str], dict[str, Any]] = {}

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

    def publish_all_discovery(self, device_id: str) -> None:
        for table, key, metadata in self._sensor_loader.iter_sensors():
            if table in ISNEW_TABLES:
                continue
            if not self._table_enabled_for_device(device_id, table):
                continue
            target_device_id = self._target_device_id(device_id, table)
            sensor_name = metadata.get("name_cs") or metadata.get("name") or key
            unit = metadata.get("unit_of_measurement") or ""
            device_class = metadata.get("device_class") or ""
            state_class = metadata.get("state_class") or ""
            is_binary = bool(metadata.get("is_binary", False))
            entity_category = metadata.get("entity_category") or ""
            device_mapping = metadata.get("device_mapping") or ""
            enum_map = metadata.get("enum_map") or None
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

    async def process(self, device_id: str, table: str, data: dict[str, Any]) -> None:
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

        if table in ISNEW_TABLES:
            table = "tbl_actual"

        if table == "tbl_batt_prms":
            bat_n = data.get("BAT_N")
            if isinstance(bat_n, (int, float)):
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
            data = deduped

        for key, value in data.items():
            # Skip internal keys prefixed with _
            if key.startswith("_"):
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

            # Add value to publish data
            pub_data[key] = value

            # Check for warnings_3f and decode if present
            warnings_list = metadata.get("warnings_3f", [])
            if warnings_list and isinstance(value, int):
                warnings = decode_warnings(value, warnings_list)
                if warnings:
                    pub_data[f"{key}_warnings"] = warnings
                    details = decode_warning_details(value, warnings_list)
                    warnings_cs = [
                        item.get("remark_cs") or item.get("remark") or item.get("key")
                        for item in details
                    ]
                    warning_codes = [
                        item.get("warning_code")
                        for item in details
                        if item.get("warning_code") is not None
                    ]
                    if warnings_cs:
                        pub_data[f"{key}_warnings_cs"] = warnings_cs
                    if warning_codes:
                        pub_data[f"{key}_warning_codes"] = warning_codes
                    logger.debug(
                        "Decoded warnings for %s:%s -> %s", table, key, warnings
                    )

        # Publish state if we have any data
        if pub_data:
            self._mqtt.publish_state(target_device_id, table, pub_data)
            self._last_table_values[(device_id, table)] = {
                k: v for k, v in pub_data.items() if not k.startswith("_")
            }
            logger.debug("Published %d keys for %s:%s", len(pub_data), target_device_id, table)

            if table == "tbl_actual":
                mirror_payloads: dict[str, dict[str, Any]] = {}
                for key, value in pub_data.items():
                    if key.endswith("_warnings") or key.endswith("_warnings_cs") or key.endswith("_warning_codes"):
                        continue
                    mirror_table = self._actual_mirror_targets.get(key)
                    if not mirror_table:
                        continue
                    if self._sensor_loader.lookup(mirror_table, key) is None:
                        continue
                    mirror_payloads.setdefault(mirror_table, {})[key] = value

                for mirror_table, mirror_data in mirror_payloads.items():
                    self._mqtt.publish_state(target_device_id, mirror_table, mirror_data)
                    logger.debug(
                        "Mirrored %d keys from tbl_actual to %s for %s",
                        len(mirror_data),
                        mirror_table,
                        target_device_id,
                    )
