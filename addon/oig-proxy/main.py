#!/usr/bin/env python3
"""
OIG Cloud Proxy - TCP proxy s MQTT integrací pro Home Assistant.
Senzory definovány podle oficiální OIG dokumentace.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import re
import sqlite3
import sys
import time
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
MQTT_NAMESPACE = os.getenv("MQTT_NAMESPACE", "oig_local")
SENSOR_MAP_PATH = os.getenv(
    "SENSOR_MAP_PATH", os.path.join(os.path.dirname(__file__), "sensor_map.json")
)
MAP_RELOAD_SECONDS = int(os.getenv("MAP_RELOAD_SECONDS", "0"))  # 0 = vypnuto
WARNING_MAP: dict[str, list[dict[str, Any]]] = {}
CAPTURE_PAYLOADS = os.getenv("CAPTURE_PAYLOADS", "false").lower() == "true"
CAPTURE_DB_PATH = "/data/payloads.db"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
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


_last_map_load = 0.0


def _iso_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _init_capture_db() -> tuple[sqlite3.Connection | None, set[str]]:
    """Inicializuje SQLite DB pro capture; vrací connection a sadu dostupných sloupců."""
    if not CAPTURE_PAYLOADS:
        return None, set()
    try:
        conn = sqlite3.connect(CAPTURE_DB_PATH, check_same_thread=False)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                device_id TEXT,
                table_name TEXT,
                raw TEXT,
                parsed TEXT,
                direction TEXT,
                conn_id INTEGER,
                peer TEXT,
                length INTEGER
            )
            """
        )
        # pokud vznikla ze starší verze, pokusíme se přidat chybějící sloupce
        for col_name, col_type in [
            ("direction", "TEXT"),
            ("conn_id", "INTEGER"),
            ("peer", "TEXT"),
            ("length", "INTEGER"),
        ]:
            try:
                conn.execute(f"ALTER TABLE frames ADD COLUMN {col_name} {col_type}")
                conn.commit()
            except Exception:
                pass
        cols = {row[1] for row in conn.execute("PRAGMA table_info(frames)")}
        return conn, cols
    except Exception as e:
        logger.warning(f"Init capture DB failed: {e}")
        return None, set()


_capture_conn, _capture_cols = _init_capture_db()


def capture_payload(
    device_id: str | None,
    table: str | None,
    raw: str,
    parsed: dict[str, Any],
    direction: str | None = None,
    conn_id: int | None = None,
    peer: str | None = None,
    length: int | None = None,
) -> None:
    if not CAPTURE_PAYLOADS or not _capture_conn:
        return
    try:
        ts = _iso_now()
        fields = ["ts", "device_id", "table_name", "raw", "parsed"]
        values = [ts, device_id, table, raw, json.dumps(parsed, ensure_ascii=False)]
        if "direction" in _capture_cols:
            fields.append("direction")
            values.append(direction)
        if "conn_id" in _capture_cols:
            fields.append("conn_id")
            values.append(conn_id)
        if "peer" in _capture_cols:
            fields.append("peer")
            values.append(peer)
        if "length" in _capture_cols:
            fields.append("length")
            values.append(length)
        placeholders = ",".join("?" for _ in fields)
        sql = f"INSERT INTO frames ({','.join(fields)}) VALUES ({placeholders})"
        _capture_conn.execute(sql, values)
        _capture_conn.commit()
    except Exception as e:
        logger.debug(f"Capture payload failed: {e}")
def load_unknown_registry() -> None:
    """Deprecated placeholder, unknown sensors se neregistrují."""
    return


def get_sensor_config(sensor_id: str, table: str | None = None) -> SensorConfig | None:
    config = SENSORS.get(sensor_id)
    if config:
        return config
    return None


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
        remark = item.get("remark_cs") or item.get("remark")
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
            name = meta.get("name_cs") or meta.get("name") or _friendly_name(sid)
            unit = meta.get("unit_of_measurement") or ""
            device_class = meta.get("device_class")
            state_class = meta.get("state_class")
            icon = meta.get("icon")
            SENSORS[sid] = SensorConfig(name, unit, device_class, state_class, icon)
            added += 1
        if added:
            logger.info(f"Doplněno/aktualizováno {added} senzorů z JSON mappingu")
        # warningy pro dekódování bitů chyb (např. ERR_PV)
        WARNING_MAP = {}
        for w in mapping.get("warnings_3f", []):
            key = w.get("table_key") or w.get("key")
            bit = w.get("bit")
            remark = w.get("remark")
            remark_cs = w.get("remark_cs")
            code = w.get("warning_code")
            if not key or bit is None:
                continue
            WARNING_MAP.setdefault(key, []).append(
                {"bit": int(bit), "remark": remark, "remark_cs": remark_cs, "code": code}
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


AUTO_DISCOVER_UNKNOWN = False

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
for sid, cfg in EXTRA_SENSORS.items():
    if sid not in SENSORS:
        SENSORS[sid] = cfg


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
            self.client = mqtt.Client(client_id=f"{MQTT_NAMESPACE}_{self.device_id}")
            if MQTT_USERNAME:
                self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            availability_topic = f"{MQTT_NAMESPACE}/{self.device_id}/availability"
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
            client.publish(f"{MQTT_NAMESPACE}/{self.device_id}/availability", "online", retain=True)
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
        unique_id = f"{MQTT_NAMESPACE}_{self.device_id}_{sensor_id.lower()}"
        discovery_payload = {
            "name": config.name,
            "unique_id": unique_id,
            "state_topic": f"{MQTT_NAMESPACE}/{self.device_id}/state",
            "value_template": f"{{{{ value_json.{sensor_id} }}}}",
            "availability": [{"topic": f"{MQTT_NAMESPACE}/{self.device_id}/availability"}],
            "device": {
                "identifiers": [f"{MQTT_NAMESPACE}_{self.device_id}"],
                "name": f"{MQTT_NAMESPACE}_{self.device_id}",
                "manufacturer": "OIG Power",
                "model": "OIG BatteryBox",
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
        topic = f"{MQTT_NAMESPACE}/{self.device_id}/state"
        self.client.publish(topic, json.dumps(data))


class OIGProxy:
    def __init__(self) -> None:
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
        tasks: list[asyncio.Task[Any]] = []
        server_writer: asyncio.StreamWriter | None = None
        try:
            server_reader, server_writer = await asyncio.open_connection(
                TARGET_SERVER, TARGET_PORT
            )
            logger.info(f"[#{conn_id}] Připojeno k {TARGET_SERVER}:{TARGET_PORT}")
            tasks = [
                asyncio.create_task(
                    self._forward(client_reader, server_writer, conn_id, "BOX→CLOUD", peer=str(client_addr))
                ),
                asyncio.create_task(
                    self._forward(
                        server_reader,
                        client_writer,
                        conn_id,
                        "CLOUD→BOX",
                        peer=str(server_writer.get_extra_info("peername")),
                    )
                ),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.gather(*done, return_exceptions=True)
        except Exception as e:
            logger.error(f"[#{conn_id}] Chyba: {e}")
        finally:
            if server_writer:
                try:
                    server_writer.close()
                    await server_writer.wait_closed()
                except Exception:
                    pass
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except Exception:
                pass
            logger.info(f"[#{conn_id}] Spojení ukončeno")

    async def _forward(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        conn_id: int,
        direction: str,
        peer: str | None = None,
    ) -> None:
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                if direction == "BOX→CLOUD":
                    handled = self._process_data(data, conn_id, peer)
                    if not handled:
                        capture_payload(
                            self.device_id,
                            None,
                            data.decode("utf-8", errors="ignore"),
                            {},
                            direction="box_to_cloud",
                            conn_id=conn_id,
                            peer=peer,
                            length=len(data),
                        )
                elif direction == "CLOUD→BOX":
                    capture_payload(
                        self.device_id,
                        None,
                        data.decode("utf-8", errors="ignore"),
                        {},
                        direction="proxy_to_box",
                        conn_id=conn_id,
                        peer=peer,
                        length=len(data),
                    )
                writer.write(data)
                await writer.drain()
        except Exception as e:
            logger.debug(f"[#{conn_id}] {direction} forward ukončen: {e}")

    def _process_data(self, data: bytes, conn_id: int, peer: str | None = None) -> bool:
        try:
            load_sensor_map()  # případný reload mapy za běhu
            text = data.decode("utf-8", errors="ignore")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"[#{conn_id}] RAW: {text}")
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
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(f"[#{conn_id}] PARSED ({table}): {parsed}")
                    capture_payload(device_id, table, text, parsed, direction="box_to_proxy")
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
                    return True
        except Exception as e:
            logger.error(f"[#{conn_id}] Chyba parsování: {e}")
        return False

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
