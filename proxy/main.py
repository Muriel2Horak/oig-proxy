#!/usr/bin/env python3
"""
OIG Cloud Proxy - TCP proxy s MQTT integrací pro Home Assistant.
Senzory definovány podle oficiální OIG dokumentace.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import datetime
from dataclasses import dataclass
from typing import Any

MQTT_HOST = os.getenv("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
TARGET_SERVER = os.getenv("TARGET_SERVER", "oigservis.cz")
TARGET_PORT = int(os.getenv("TARGET_PORT", "5710"))
PROXY_PORT = int(os.getenv("PROXY_PORT", "5710"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
SENSOR_MAP_PATH = os.getenv(
    "SENSOR_MAP_PATH", os.path.join(os.path.dirname(__file__), "sensor_map.json")
)
MAP_RELOAD_SECONDS = int(os.getenv("MAP_RELOAD_SECONDS", "0"))  # 0 = vypnuto
UNKNOWN_SENSORS_PATH = os.getenv(
    "UNKNOWN_SENSORS_PATH", "/data/unknown_sensors.json"
)
WARNING_MAP: dict[str, list[dict[str, Any]]] = {}

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    logger.warning("paho-mqtt není nainstalován")


def _friendly_name(sensor_id: str) -> str:
    parts = sensor_id.replace("_", " ").split()
    return " ".join(p.capitalize() for p in parts)


unknown_registry: dict[str, dict[str, Any]] = {}
_last_map_load = 0.0


def _iso_now() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def load_unknown_registry() -> None:
    """Načte registry neznámých senzorů z disku."""
    global unknown_registry
    if not os.path.exists(UNKNOWN_SENSORS_PATH):
        unknown_registry = {}
        return
    try:
        with open(UNKNOWN_SENSORS_PATH, "r", encoding="utf-8") as f:
            unknown_registry = json.load(f)
    except Exception as e:
        logger.warning(f"Načtení unknown_sensors selhalo: {e}")
        unknown_registry = {}


def save_unknown_registry() -> None:
    try:
        os.makedirs(os.path.dirname(UNKNOWN_SENSORS_PATH), exist_ok=True)
        with open(UNKNOWN_SENSORS_PATH, "w", encoding="utf-8") as f:
            json.dump(unknown_registry, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Uložení unknown_sensors selhalo: {e}")


def record_unknown(sensor_id: str, table: str | None = None) -> None:
    """Uloží neznámý senzor do registru pro pozdější mapování."""
    entry = unknown_registry.get(sensor_id, {})
    now = _iso_now()
    entry["count"] = entry.get("count", 0) + 1
    entry["first_seen"] = entry.get("first_seen") or now
    entry["last_seen"] = now
    tables = set(entry.get("tables", []))
    if table:
        tables.add(table)
    entry["tables"] = sorted(tables)
    unknown_registry[sensor_id] = entry
    save_unknown_registry()


def get_sensor_config(sensor_id: str, table: str | None = None) -> SensorConfig | None:
    config = SENSORS.get(sensor_id)
    if config:
        return config
    if not AUTO_DISCOVER_UNKNOWN:
        return None
    record_unknown(sensor_id, table)
    config = SensorConfig(_friendly_name(sensor_id), "")
    SENSORS[sensor_id] = config
    logger.debug(f"Auto-registrace senzoru {sensor_id} (generic)")
    return config


def decode_warnings(key: str, value: Any) -> list[str]:
    """Dekóduje bitové chyby podle WARNING_MAP a vrací seznam textů."""
    if key not in WARNING_MAP:
        return []
    try:
        val_int = int(value)
    except Exception:
        return []
    texts: list[str] = []
    for item in WARNING_MAP.get(key, []):
        bit = item.get("bit")
        remark = item.get("remark")
        if bit is None:
            continue
        if val_int & int(bit):
            if remark:
                texts.append(remark)
    return texts


def load_sensor_map() -> None:
    """Načte mapping z JSON (vygenerovaný z Excelu) a doplní SENSORS."""
    global _last_map_load
    global WARNING_MAP
    now = time.time()
    if MAP_RELOAD_SECONDS > 0 and (now - _last_map_load) < MAP_RELOAD_SECONDS:
        return
    if not os.path.exists(SENSOR_MAP_PATH):
        logger.info(f"JSON mapping nenalezen, přeskočeno ({SENSOR_MAP_PATH})")
        return
    try:
        with open(SENSOR_MAP_PATH, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        sensors = mapping.get("sensors", {})
        added = 0
        for sid, meta in sensors.items():
            if sid in SENSORS:
                continue
            name = meta.get("description") or _friendly_name(sid)
            unit = meta.get("unit") or ""
            SENSORS[sid] = SensorConfig(name, unit)
            added += 1
        if added:
            logger.info(f"Doplněno {added} senzorů z JSON mappingu")
        # warningy pro dekódování bitů chyb (např. ERR_PV)
        WARNING_MAP = {}
        for w in mapping.get("warnings_3f", []):
            key = w.get("table_key") or w.get("key")
            bit = w.get("bit")
            remark = w.get("remark")
            code = w.get("warning_code")
            if not key or bit is None:
                continue
            WARNING_MAP.setdefault(key, []).append(
                {"bit": int(bit), "remark": remark, "code": code}
            )
        _last_map_load = now
    except Exception as e:
        logger.warning(f"Načtení mappingu selhalo: {e}")


@dataclass
class SensorConfig:
    name: str
    unit: str
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None


AUTO_DISCOVER_UNKNOWN = True

# Senzory preferujeme načítat z JSON mappingu (SENSOR_MAP_PATH), jen doplňky níže.
SENSORS: dict[str, SensorConfig] = {}

EXTRA_SENSORS: dict[str, SensorConfig] = {
    "Type": SensorConfig("Typ události", "", None, "measurement", "mdi:alert-circle"),
    "Confirm": SensorConfig("Potvrzení události", "", None, "measurement", "mdi:check-circle"),
    "Content": SensorConfig("Obsah události", "", None, "measurement", "mdi:message-text"),
    "Result": SensorConfig("Výsledek", "", None, "measurement", "mdi:check"),
    "ToDo": SensorConfig("Úloha", "", None, "measurement", "mdi:clipboard-text"),
    "Lat": SensorConfig("Latence", "ms", None, "measurement", "mdi:timer"),
    "Rdt": SensorConfig("Čas dat", "", None, "measurement", "mdi:clock"),
    "Tmr": SensorConfig("Timer", "", None, "measurement", "mdi:timer"),
    "ENBL": SensorConfig("Systém povolen", "", None, "measurement", "mdi:power"),
    "VIZ": SensorConfig("Vizualizace", "", None, "measurement", "mdi:eye"),
    "STAT": SensorConfig("Status", "", None, "measurement", "mdi:information"),
    "ACO_P": SensorConfig("Spotřeba celkem", "W", "power", "measurement", "mdi:home-lightning-bolt"),
}

load_sensor_map()
SENSORS.update(EXTRA_SENSORS)


class OIGDataParser:
    @staticmethod
    def parse_xml_frame(data: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        tbl_match = re.search(r"<TblName>([^<]+)</TblName>", data)
        if tbl_match:
            result["_table"] = tbl_match.group(1)
        id_match = re.search(r"<ID_Device>(\d+)</ID_Device>", data)
        if id_match:
            result["_device_id"] = id_match.group(1)
        for match in re.finditer(r"<([A-Za-z_0-9]+)>([^<]*)</\1>", data):
            key, value = match.groups()
            if key in ("TblName", "ID_Device", "ID_Set", "Reason", "ver", "CRC", "DT"):
                continue
            try:
                if "." in value:
                    result[key] = float(value)
                else:
                    result[key] = int(value)
            except ValueError:
                result[key] = value
        return result


class MQTTPublisher:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.client: mqtt.Client | None = None
        self.connected = False
        self.discovery_sent: set[str] = set()

    def connect(self) -> bool:
        if not MQTT_AVAILABLE:
            return False
        try:
            self.client = mqtt.Client(client_id=f"oig_proxy_{self.device_id}")
            if MQTT_USERNAME:
                self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            availability_topic = f"oig_box/{self.device_id}/availability"
            self.client.will_set(availability_topic, "offline", retain=True)
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            logger.info(f"Připojuji k MQTT {MQTT_HOST}:{MQTT_PORT}")
            self.client.connect(MQTT_HOST, MQTT_PORT, 60)
            self.client.loop_start()
            return True
        except Exception as e:
            logger.error(f"MQTT připojení selhalo: {e}")
            return False

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int) -> None:
        if rc == 0:
            logger.info("MQTT připojeno")
            self.connected = True
            # Označit zařízení jako online
            client.publish(f"oig_box/{self.device_id}/availability", "online", retain=True)
            # Po novém připojení chceme poslat discovery znovu (např. při změně popisků)
            self.discovery_sent.clear()
        else:
            logger.error(f"MQTT připojení selhalo s kódem {rc}")

    def _on_disconnect(self, client: Any, userdata: Any, rc: int) -> None:
        logger.warning(f"MQTT odpojeno (rc={rc})")
        self.connected = False

    def send_discovery(self, sensor_id: str, config: SensorConfig) -> None:
        if not self.client or not self.connected:
            return
        if sensor_id in self.discovery_sent:
            return
        unique_id = f"oig_{self.device_id}_{sensor_id.lower()}"
        discovery_payload = {
            "name": config.name,
            "unique_id": unique_id,
            "state_topic": f"oig_box/{self.device_id}/state",
            "value_template": f"{{{{ value_json.{sensor_id} }}}}",
            "availability": [{"topic": f"oig_box/{self.device_id}/availability"}],
            "device": {
                "identifiers": [f"oig_box_{self.device_id}"],
                "name": f"OIG BatteryBox {self.device_id}",
                "manufacturer": "OIG Power",
                "model": "BatteryBox",
            },
        }
        if config.unit:
            discovery_payload["unit_of_measurement"] = config.unit
        if config.device_class:
            discovery_payload["device_class"] = config.device_class
        if config.state_class:
            discovery_payload["state_class"] = config.state_class
        if config.icon:
            discovery_payload["icon"] = config.icon
        topic = f"homeassistant/sensor/{unique_id}/config"
        self.client.publish(topic, json.dumps(discovery_payload), retain=True)
        self.discovery_sent.add(sensor_id)
        logger.debug(f"Discovery odesláno pro {sensor_id}")

    def publish_data(self, data: dict[str, Any]) -> None:
        if not self.client or not self.connected:
            return
        for key in data:
            if key.startswith("_"):
                continue
            cfg = get_sensor_config(key)
            if cfg:
                self.send_discovery(key, cfg)
        topic = f"oig_box/{self.device_id}/state"
        self.client.publish(topic, json.dumps(data))


class OIGProxy:
    def __init__(self) -> None:
        load_unknown_registry()
        self.parser = OIGDataParser()
        self.mqtt_publisher: MQTTPublisher | None = None
        self.connection_count = 0
        self.device_id: str | None = None
        self.current_state: dict[str, Any] = {}

    async def handle_connection(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        self.connection_count += 1
        conn_id = self.connection_count
        client_addr = client_writer.get_extra_info("peername")
        logger.info(f"[#{conn_id}] Nové připojení od {client_addr}")
        try:
            server_reader, server_writer = await asyncio.open_connection(TARGET_SERVER, TARGET_PORT)
            logger.info(f"[#{conn_id}] Připojeno k {TARGET_SERVER}:{TARGET_PORT}")
            await asyncio.gather(
                self._forward(client_reader, server_writer, conn_id, "BOX→CLOUD"),
                self._forward(server_reader, client_writer, conn_id, "CLOUD→BOX"),
                return_exceptions=True,
            )
        except Exception as e:
            logger.error(f"[#{conn_id}] Chyba: {e}")
        finally:
            client_writer.close()
            logger.info(f"[#{conn_id}] Spojení ukončeno")

    async def _forward(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, conn_id: int, direction: str
    ) -> None:
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                if direction == "BOX→CLOUD":
                    self._process_data(data, conn_id)
                writer.write(data)
                await writer.drain()
        except Exception as e:
            logger.debug(f"[#{conn_id}] {direction} forward ukončen: {e}")

    def _process_data(self, data: bytes, conn_id: int) -> None:
        try:
            load_sensor_map()  # případný reload mapy za běhu
            text = data.decode("utf-8", errors="ignore")
            if "<Frame>" in text:
                parsed = self.parser.parse_xml_frame(text)
                if parsed:
                    table = parsed.get("_table", "unknown")
                    device_id = parsed.get("_device_id")
                    if device_id and not self.mqtt_publisher:
                        self.device_id = device_id
                        self.mqtt_publisher = MQTTPublisher(device_id)
                        self.mqtt_publisher.connect()
                    for key, value in parsed.items():
                        if not key.startswith("_"):
                            self.current_state[key] = value
                    logger.info(f"[#{conn_id}] 📊 {table}: {len(parsed)-2} hodnot")
                    # Odvozené texty chyb podle WARNING_MAP (ERR_* bitové masky)
                    for key, value in parsed.items():
                        if key in WARNING_MAP:
                            texts = decode_warnings(key, value)
                            if texts:
                                derived_key = f"{key}_warnings"
                                self.current_state[derived_key] = texts
                                get_sensor_config(derived_key)
                    if self.mqtt_publisher:
                        self.mqtt_publisher.publish_data(self.current_state)
        except Exception as e:
            logger.error(f"[#{conn_id}] Chyba parsování: {e}")

    async def start(self) -> None:
        server = await asyncio.start_server(self.handle_connection, "0.0.0.0", PROXY_PORT)
        logger.info(f"🚀 OIG Proxy naslouchá na portu {PROXY_PORT}")
        logger.info(f"   Cíl: {TARGET_SERVER}:{TARGET_PORT}")
        logger.info(f"   MQTT: {MQTT_HOST}:{MQTT_PORT}")
        logger.info(f"   Definováno {len(SENSORS)} senzorů (JSON mapping + extra)")
        async with server:
            await server.serve_forever()


async def main() -> None:
    logger.info("=" * 50)
    logger.info("OIG Cloud Proxy for Home Assistant")
    logger.info("Senzory podle oficiální OIG dokumentace")
    logger.info("=" * 50)
    proxy = OIGProxy()
    await proxy.start()


if __name__ == "__main__":
    asyncio.run(main())
