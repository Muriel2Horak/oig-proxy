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


def get_sensor_config(sensor_id: str, table: str | None = None) -> tuple[SensorConfig | None, str]:
    """Vrátí konfiguraci senzoru a jeho unikátní klíč.
    
    Pořadí vyhledávání:
    1. table:sensor_id (specifické mapování pro tabulku)
    2. sensor_id (obecné mapování, fallback)
    
    Returns:
        tuple: (SensorConfig nebo None, unikátní klíč pro senzor)
    """
    # Nejprve zkusíme specifický klíč s tabulkou
    if table:
        table_key = f"{table}:{sensor_id}"
        config = SENSORS.get(table_key)
        if config:
            return config, table_key
    
    # Fallback na obecný klíč bez tabulky
    config = SENSORS.get(sensor_id)
    if config:
        # Pokud máme tabulku, použijeme ji v klíči pro unikátnost
        unique_key = f"{table}:{sensor_id}" if table else sensor_id
        return config, unique_key
    
    return None, sensor_id


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
            device_mapping = meta.get("device_mapping")
            entity_category = meta.get("entity_category")
            options = meta.get("options")  # Pro enum
            SENSORS[sid] = SensorConfig(
                name, unit, device_class, state_class, icon,
                device_mapping, entity_category, options
            )
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
@dataclass
class SensorConfig:
    name: str
    unit: str
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None
    device_mapping: str | None = None
    entity_category: str | None = None
    options: list[str] | None = None  # Pro enum device_class


# Mapování device_mapping na názvy zařízení
DEVICE_NAMES = {
    "battery": "Baterie",
    "pv": "FVE",
    "grid": "Síť",
    "load": "Spotřeba",
    "boiler": "Bojler",
    "wallbox": "Wallbox",
    "inverter": "Střídač",
    "batterybox": "BatteryBox",
}

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
    """MQTT publisher s robustním připojením, QoS=1 a health check."""
    
    # MQTT return codes
    RC_CODES = {
        0: "Connection successful",
        1: "Incorrect protocol version",
        2: "Invalid client identifier",
        3: "Server unavailable",
        4: "Bad username or password",
        5: "Not authorized",
    }
    
    # Konfigurace
    CONNECT_TIMEOUT = 5  # Timeout pro připojení (sekundy)
    HEALTH_CHECK_INTERVAL = 30  # Interval health check při výpadku (sekundy)
    PUBLISH_QOS = 1  # QoS level pro publish (0=fire&forget, 1=at least once)
    PUBLISH_LOG_EVERY = 100  # Logovat každý N-tý úspěšný publish
    
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.client: mqtt.Client | None = None
        self.connected = False
        self.discovery_sent: set[str] = set()
        # Statistiky
        self.publish_count = 0
        self.publish_success = 0
        self.publish_failed = 0
        self.last_publish_time: float = 0
        self.last_error_time: float = 0
        self.last_error_msg: str = ""
        self.reconnect_attempts = 0
        # Health check
        self._health_check_task: asyncio.Task[Any] | None = None
        self._connect_event: asyncio.Event | None = None

    def connect(self, timeout: float | None = None) -> bool:
        """Připojí k MQTT brokeru s timeoutem. Vrací True pokud úspěšně."""
        if not MQTT_AVAILABLE:
            logger.error("MQTT knihovna paho-mqtt není nainstalována")
            return False
        
        timeout = timeout or self.CONNECT_TIMEOUT
        
        try:
            # paho-mqtt 2.x vyžaduje callback_api_version, protokol 3.1.1 pro kompatibilitu
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=f"{MQTT_NAMESPACE}_{self.device_id}",
                protocol=mqtt.MQTTv311
            )
            if MQTT_USERNAME:
                self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
                logger.debug(f"MQTT autentizace: user={MQTT_USERNAME}")
            
            availability_topic = f"{MQTT_NAMESPACE}/{self.device_id}/availability"
            self.client.will_set(availability_topic, "offline", retain=True)
            
            # Callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_publish = self._on_publish
            
            logger.info(f"MQTT: Připojuji k {MQTT_HOST}:{MQTT_PORT} (timeout {timeout}s)")
            
            # Synchronní připojení s čekáním
            self._connect_event = asyncio.Event()
            self.client.connect(MQTT_HOST, MQTT_PORT, 60)
            self.client.loop_start()
            
            # Čekáme na callback _on_connect
            start = time.time()
            while not self.connected and (time.time() - start) < timeout:
                time.sleep(0.1)
            
            if self.connected:
                logger.info(f"MQTT: ✅ Připojeno k {MQTT_HOST}:{MQTT_PORT}")
                self.reconnect_attempts = 0
                return True
            else:
                logger.error(f"MQTT: ❌ Timeout připojení po {timeout}s")
                self._cleanup_client()
                return False
                
        except Exception as e:
            logger.error(f"MQTT: ❌ Připojení selhalo: {e}")
            self._cleanup_client()
            return False

    def _cleanup_client(self) -> None:
        """Bezpečně uklidí MQTT klienta."""
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
            self.client = None
        self.connected = False

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int) -> None:
        rc_msg = self.RC_CODES.get(rc, f"Unknown error ({rc})")
        
        if rc == 0:
            logger.info(f"MQTT: Připojeno (flags={flags})")
            self.connected = True
            self.reconnect_attempts = 0
            # Označit zařízení jako online
            result = client.publish(
                f"{MQTT_NAMESPACE}/{self.device_id}/availability", 
                "online", 
                retain=True, 
                qos=1
            )
            logger.debug(f"MQTT: Availability online (mid={result.mid})")
            # Reset discovery - při reconnectu odeslat znovu
            self.discovery_sent.clear()
            logger.info(f"MQTT: Discovery cache vyčištěna, připraveno k odesílání")
        else:
            logger.error(f"MQTT: ❌ Připojení odmítnuto: {rc_msg}")
            self.connected = False
            self.last_error_time = time.time()
            self.last_error_msg = rc_msg

    def _on_disconnect(self, client: Any, userdata: Any, rc: int) -> None:
        was_connected = self.connected
        self.connected = False
        
        if rc == 0:
            logger.info("MQTT: Odpojeno (čisté odpojení)")
        else:
            logger.warning(f"MQTT: ⚠️ Neočekávané odpojení (rc={rc})")
            self.last_error_time = time.time()
            self.last_error_msg = f"Unexpected disconnect (rc={rc})"
            
        if was_connected:
            logger.warning("MQTT: 🔴 Zpracování dat pozastaveno do obnovení spojení")

    def _on_publish(self, client: Any, userdata: Any, mid: int) -> None:
        """Callback při potvrzení publish od brokera (QoS >= 1)."""
        self.publish_success += 1
        self.last_publish_time = time.time()
        
        # Logovat každý N-tý publish
        if self.publish_success % self.PUBLISH_LOG_EVERY == 0:
            logger.info(
                f"MQTT: 📊 Stats: {self.publish_success} OK, "
                f"{self.publish_failed} FAIL z {self.publish_count} celkem"
            )

    def is_ready(self) -> bool:
        """Vrací True pokud je MQTT připraveno k publikování."""
        return self.client is not None and self.connected

    def get_status(self) -> dict[str, Any]:
        """Vrátí status MQTT publisheru pro diagnostiku."""
        return {
            "connected": self.connected,
            "publish_count": self.publish_count,
            "publish_success": self.publish_success,
            "publish_failed": self.publish_failed,
            "success_rate": f"{(self.publish_success / max(1, self.publish_count)) * 100:.1f}%",
            "last_publish": datetime.datetime.fromtimestamp(self.last_publish_time).isoformat() if self.last_publish_time else None,
            "last_error": self.last_error_msg if self.last_error_msg else None,
            "reconnect_attempts": self.reconnect_attempts,
        }

    async def health_check_loop(self) -> None:
        """Periodicky kontroluje MQTT spojení a pokouší se o reconnect."""
        logger.info(f"MQTT: Health check spuštěn (interval {self.HEALTH_CHECK_INTERVAL}s při výpadku)")
        
        while True:
            await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)
            
            if not self.connected:
                self.reconnect_attempts += 1
                logger.warning(
                    f"MQTT: 🔄 Health check - pokus o reconnect #{self.reconnect_attempts}"
                )
                
                if self.connect(timeout=self.CONNECT_TIMEOUT):
                    logger.info(f"MQTT: ✅ Reconnect úspěšný po {self.reconnect_attempts} pokusech")
                else:
                    logger.warning(
                        f"MQTT: ❌ Reconnect selhal, další pokus za {self.HEALTH_CHECK_INTERVAL}s"
                    )
            else:
                # Logovat health status každých 5 minut (10 * 30s)
                if self.reconnect_attempts == 0 and self.publish_count > 0:
                    if self.publish_count % (self.PUBLISH_LOG_EVERY * 10) < self.PUBLISH_LOG_EVERY:
                        status = self.get_status()
                        logger.debug(f"MQTT: Health OK - {status['success_rate']} success rate")

    def start_health_check(self) -> None:
        """Spustí health check jako background task."""
        if self._health_check_task is None or self._health_check_task.done():
            self._health_check_task = asyncio.create_task(self.health_check_loop())
            logger.debug("MQTT: Health check task spuštěn")

    def send_discovery(self, sensor_id: str, config: SensorConfig) -> None:
        if not self.client or not self.connected:
            return
        if sensor_id in self.discovery_sent:
            return
        
        # Určit device podle device_mapping
        device_type = config.device_mapping or "inverter"
        device_suffix = device_type
        device_name = DEVICE_NAMES.get(device_type, "Střídač")
        
        # Identifikátory zařízení
        device_identifier = f"{MQTT_NAMESPACE}_{self.device_id}_{device_suffix}"
        full_device_name = f"OIG {device_name} ({self.device_id})"
        
        # Pro unique_id nahradíme dvojtečku podtržítkem (HA kompatibilita)
        safe_sensor_id = sensor_id.replace(":", "_").lower()
        unique_id = f"{MQTT_NAMESPACE}_{self.device_id}_{safe_sensor_id}"
        object_id = f"{MQTT_NAMESPACE}_{self.device_id}_{safe_sensor_id}"
        availability_topic = f"{MQTT_NAMESPACE}/{self.device_id}/availability"
        
        # Value template - klíč s dvojtečkou musí být v hranatých závorkách
        if ":" in sensor_id:
            value_template = f"{{{{ value_json['{sensor_id}'] }}}}"
        else:
            value_template = f"{{{{ value_json.{sensor_id} }}}}"
        
        discovery_payload = {
            "name": config.name,
            "object_id": object_id,
            "unique_id": unique_id,
            "state_topic": f"{MQTT_NAMESPACE}/{self.device_id}/state",
            "value_template": value_template,
            "availability": [{"topic": availability_topic}],
            "device": {
                "identifiers": [device_identifier],
                "name": full_device_name,
                "manufacturer": "OIG Power",
                "model": f"OIG BatteryBox - {device_name}",
                "via_device": f"{MQTT_NAMESPACE}_{self.device_id}_inverter",
            },
        }
        
        # Hlavní zařízení (inverter) nemá via_device
        if device_type == "inverter":
            del discovery_payload["device"]["via_device"]
        
        if config.unit:
            discovery_payload["unit_of_measurement"] = config.unit
        if config.device_class:
            discovery_payload["device_class"] = config.device_class
        if config.state_class:
            discovery_payload["state_class"] = config.state_class
        if config.icon:
            discovery_payload["icon"] = config.icon
        if config.entity_category:
            discovery_payload["entity_category"] = config.entity_category
        if config.options:
            discovery_payload["options"] = config.options
            
        topic = f"homeassistant/sensor/{unique_id}/config"
        result = self.client.publish(
            topic, json.dumps(discovery_payload), retain=True, qos=self.PUBLISH_QOS
        )
        self.discovery_sent.add(sensor_id)
        logger.debug(f"MQTT: Discovery {sensor_id} → {device_name} (mid={result.mid})")

    def publish_data(self, data: dict[str, Any]) -> bool:
        """Publikuje data na MQTT. Vrací True pokud úspěšně odesláno."""
        if not self.is_ready():
            if self.publish_count == 0 or self.publish_failed % 100 == 0:
                logger.warning("MQTT: Nelze publikovat - není připojeno")
            self.publish_failed += 1
            return False
            
        table = data.get("_table")
        # Připravíme data pro publikování s unikátními klíči
        publish_data = {}
        for key in data:
            if key.startswith("_"):
                continue
            cfg, unique_key = get_sensor_config(key, table)
            if cfg:
                self.send_discovery(unique_key, cfg)
                value = data[key]
                # Konverze enum hodnot (číslo → text)
                if cfg.options and isinstance(value, int):
                    if 0 <= value < len(cfg.options):
                        value = cfg.options[value]
                publish_data[unique_key] = value
            else:
                # Senzory bez konfigurace publikujeme s prefixem tabulky
                if table:
                    unique_key = f"{table}:{key}"
                else:
                    unique_key = key
                publish_data[unique_key] = data[key]
        
        topic = f"{MQTT_NAMESPACE}/{self.device_id}/state"
        self.publish_count += 1
        
        try:
            result = self.client.publish(
                topic, json.dumps(publish_data), qos=self.PUBLISH_QOS
            )
            # rc == 0 znamená že zpráva je ve frontě k odeslání
            if result.rc == 0:
                logger.debug(
                    f"MQTT: Publish {table} ({len(publish_data)} keys, "
                    f"mid={result.mid})"
                )
                return True
            else:
                self.publish_failed += 1
                logger.error(
                    f"MQTT: Publish selhal rc={result.rc} pro {table}"
                )
                return False
        except Exception as e:
            self.publish_failed += 1
            self.last_error_time = time.time()
            self.last_error_msg = str(e)
            logger.error(f"MQTT: Publish exception: {e}")
            return False


class OIGProxy:
    def __init__(self) -> None:
        self.parser = OIGDataParser()
        self.mqtt_publisher: MQTTPublisher | None = None
        self.connection_count = 0
        self.device_id: str | None = None
        self.current_state: dict[str, Any] = {}
        self._mqtt_warning_logged = False  # Pro throttling MQTT offline varování

    async def handle_connection(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter
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

    def _process_data(
        self, data: bytes, conn_id: int, peer: str | None = None
    ) -> bool:
        """Zpracuje data z boxu. Vrací True pokud byl frame úspěšně zparsován."""
        try:
            load_sensor_map()  # případný reload mapy za běhu
            text = data.decode("utf-8", errors="ignore")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"[#{conn_id}] RAW: {text}")
            if "<Frame>" in text:
                parsed = self.parser.parse_xml_frame(text)
                if parsed:
                    table = parsed.get("_table")
                    # Speciální zachycení ACK rámců bez TblName
                    if (not table and parsed.get("Result") == "ACK"
                            and parsed.get("ToDo") == "GetActual"):
                        table = "ack_getactual"
                    if not table:
                        table = "unknown"
                    device_id = parsed.get("_device_id")
                    
                    # Inicializace MQTT publisheru při prvním device_id
                    if device_id and not self.mqtt_publisher:
                        self.device_id = device_id
                        self._init_mqtt(device_id)
                    
                    for key, value in parsed.items():
                        if not key.startswith("_"):
                            self.current_state[key] = value
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(f"[#{conn_id}] PARSED ({table}): {parsed}")
                    capture_payload(
                        device_id,
                        table,
                        text,
                        parsed,
                        direction="box_to_proxy",
                        conn_id=conn_id,
                        peer=peer,
                        length=len(data),
                    )
                    
                    # Logování - jen pokud MQTT ready nebo každý 10. frame
                    if self.mqtt_publisher and self.mqtt_publisher.is_ready():
                        logger.info(
                            f"[#{conn_id}] 📊 {table}: {len(parsed)-2} hodnot"
                        )
                    elif not self._mqtt_warning_logged:
                        logger.warning(
                            f"[#{conn_id}] ⚠️ {table}: MQTT offline, "
                            "data se nepublikují"
                        )
                        self._mqtt_warning_logged = True
                    
                    # Odvozené texty chyb podle WARNING_MAP
                    for key, value in parsed.items():
                        if key in WARNING_MAP:
                            texts = decode_warnings(key, value)
                            if texts:
                                derived_key = f"{key}_warnings"
                                self.current_state[derived_key] = texts
                                get_sensor_config(derived_key)
                    
                    # Publikovat pouze pokud je MQTT ready
                    if self.mqtt_publisher and self.mqtt_publisher.is_ready():
                        self.mqtt_publisher.publish_data(parsed)
                        self._mqtt_warning_logged = False
                    return True
        except Exception as e:
            logger.error(f"[#{conn_id}] Chyba parsování: {e}")
        return False

    def _init_mqtt(self, device_id: str) -> None:
        """Inicializuje MQTT publisher a spustí health check."""
        logger.info(f"MQTT: Inicializuji pro device {device_id}")
        self.mqtt_publisher = MQTTPublisher(device_id)
        
        if self.mqtt_publisher.connect():
            logger.info("MQTT: ✅ Počáteční připojení úspěšné")
            self.mqtt_publisher.start_health_check()
        else:
            logger.error(
                "MQTT: ❌ Počáteční připojení selhalo, "
                "spouštím health check pro reconnect"
            )
            self.mqtt_publisher.start_health_check()

    async def start(self) -> None:
        # Pre-flight MQTT check
        await self._preflight_mqtt_check()
        
        server = await asyncio.start_server(
            self.handle_connection, "0.0.0.0", PROXY_PORT
        )
        logger.info(f"🚀 OIG Proxy naslouchá na portu {PROXY_PORT}")
        logger.info(f"   Cíl: {TARGET_SERVER}:{TARGET_PORT}")
        logger.info(f"   MQTT: {MQTT_HOST}:{MQTT_PORT}")
        logger.info(f"   Senzorů: {len(SENSORS)} (JSON mapping + extra)")
        async with server:
            await server.serve_forever()

    async def _preflight_mqtt_check(self) -> None:
        """Ověří MQTT konektivitu při startu (bez device_id)."""
        logger.info("MQTT: Pre-flight check...")
        
        if not MQTT_AVAILABLE:
            logger.error("MQTT: ❌ Knihovna paho-mqtt není dostupná!")
            return
        
        try:
            # Testovací připojení - paho-mqtt 2.x kompatibilita
            test_client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=f"{MQTT_NAMESPACE}_preflight",
                protocol=mqtt.MQTTv311
            )
            if MQTT_USERNAME:
                test_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            
            connected = False
            connect_error = None
            
            def on_connect(client, userdata, flags, rc):
                nonlocal connected, connect_error
                if rc == 0:
                    connected = True
                else:
                    connect_error = MQTTPublisher.RC_CODES.get(
                        rc, f"Unknown ({rc})"
                    )
            
            test_client.on_connect = on_connect
            test_client.connect(MQTT_HOST, MQTT_PORT, 60)
            test_client.loop_start()
            
            # Čekáme max 5s na připojení
            start = time.time()
            while not connected and connect_error is None:
                if time.time() - start > 5:
                    connect_error = "Timeout (5s)"
                    break
                await asyncio.sleep(0.1)
            
            test_client.loop_stop()
            test_client.disconnect()
            
            if connected:
                logger.info(
                    f"MQTT: ✅ Pre-flight OK - broker {MQTT_HOST}:{MQTT_PORT} "
                    "je dostupný"
                )
            else:
                logger.error(
                    f"MQTT: ❌ Pre-flight FAILED - {connect_error}"
                )
                logger.warning(
                    "MQTT: Proxy poběží, ale data se nebudou publikovat "
                    "dokud nebude MQTT dostupné"
                )
                
        except Exception as e:
            logger.error(f"MQTT: ❌ Pre-flight exception: {e}")
            logger.warning(
                "MQTT: Proxy poběží, ale data se nebudou publikovat "
                "dokud nebude MQTT dostupné"
            )


async def main() -> None:
    logger.info("=" * 50)
    logger.info("OIG Cloud Proxy for Home Assistant")
    logger.info("Senzory podle oficiální OIG dokumentace")
    logger.info("=" * 50)
    proxy = OIGProxy()
    await proxy.start()


if __name__ == "__main__":
    asyncio.run(main())
