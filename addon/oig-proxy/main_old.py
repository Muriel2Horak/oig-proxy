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
MODE_STATE_PATH = "/data/mode_state.json"

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
_current_mode: int | None = None  # Aktuální MODE hodnota (0-3)


def _iso_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _load_mode_state() -> int | None:
    """Načte uložený MODE stav z perzistentního souboru."""
    try:
        if os.path.exists(MODE_STATE_PATH):
            with open(MODE_STATE_PATH, "r") as f:
                data = json.load(f)
                mode_value = data.get("mode")
                if mode_value is not None:
                    logger.info(f"MODE: Načten uložený stav: {mode_value}")
                    return mode_value
    except Exception as e:
        logger.warning(f"MODE: Nepodařilo se načíst stav: {e}")
    return None


def _save_mode_state(mode_value: int) -> None:
    """Uloží MODE stav do perzistentního souboru."""
    try:
        os.makedirs(os.path.dirname(MODE_STATE_PATH), exist_ok=True)
        with open(MODE_STATE_PATH, "w") as f:
            json.dump({
                "mode": mode_value,
                "timestamp": _iso_now()
            }, f)
        logger.debug(f"MODE: Stav uložen: {mode_value}")
    except Exception as e:
        logger.error(f"MODE: Nepodařilo se uložit stav: {e}")


def _parse_mode_from_event(content: str) -> int | None:
    """Parsuje MODE hodnotu z tbl_events Content fieldu.
    
    Očekává formát: 'Remotely : tbl_box_prms / MODE: [old]->[new]'
    nebo 'Locally : tbl_box_prms / MODE: [old]->[new]'
    
    Returns:
        int: Nová MODE hodnota (0-3) nebo None pokud se nepodařilo parsovat
    """
    # Hledáme pattern MODE: [old]->[new]
    match = re.search(r'MODE:\s*\[(\d+)\]->\[(\d+)\]', content)
    if match:
        old_value = int(match.group(1))
        new_value = int(match.group(2))
        logger.info(f"MODE: Event detekován: {old_value} → {new_value}")
        return new_value
    return None


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
            is_binary = meta.get("is_binary", False)
            SENSORS[sid] = SensorConfig(
                name, unit, device_class, state_class, icon,
                device_mapping, entity_category, options, is_binary
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
class SensorConfig:
    name: str
    unit: str
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None
    device_mapping: str | None = None
    entity_category: str | None = None
    options: list[str] | None = None  # Pro enum device_class
    is_binary: bool = False  # True pro binary_sensor


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
        # Zachytit DT pro měření latence
        dt_match = re.search(r"<DT>([^<]+)</DT>", data)
        if dt_match:
            result["_dt"] = dt_match.group(1)
        # Kontrola ID_SubD: OIG posílá tbl_batt_prms ve 3 variantách (0,1,2)
        # pro 3 bateriové banky. Publikujeme jen SubD=0 (aktivní).
        # Viz: analysis/subd_analysis.md pro detaily o architektuře.
        subframe_match = re.search(r"<ID_SubD>(\d+)</ID_SubD>", data)
        if subframe_match:
            subframe_id = int(subframe_match.group(1))
            if subframe_id > 0:
                logger.debug(f"SubD={subframe_id} ignorován (neaktivní banka)")
                return {}  # Vrátí prázdný dict - nebude publikován
        for match in re.finditer(r"<([A-Za-z_0-9]+)>([^<]*)</\1>", data):
            key, value = match.groups()
            if key in ("TblName", "ID_Device", "ID_Set", "Reason", "ver", "CRC", "DT", "ID_SubD"):
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

    def send_discovery(
        self, sensor_id: str, config: SensorConfig, table: str | None = None
    ) -> None:
        if not self.client or not self.connected:
            return
        if sensor_id in self.discovery_sent:
            return

        # Určit typ entity (sensor vs binary_sensor) dopředu (potřebné pro default_entity_id)
        component = "binary_sensor" if config.is_binary else "sensor"
        
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
        
        # State topic - oddělený pro každou tabulku
        if table:
            state_topic = f"{MQTT_NAMESPACE}/{self.device_id}/{table}/state"
        else:
            state_topic = f"{MQTT_NAMESPACE}/{self.device_id}/state"
        
        # Value template - klíč bez prefixu tabulky (data jsou v tabulkovém topic)
        # Pokud sensor_id obsahuje ":", použijeme jen část za dvojtečkou
        if ":" in sensor_id:
            json_key = sensor_id.split(":", 1)[1]
        else:
            json_key = sensor_id
        value_template = f"{{{{ value_json.{json_key} }}}}"
        
        discovery_payload = {
            "name": config.name,
            "unique_id": unique_id,
            "state_topic": state_topic,
            "value_template": value_template,
            "availability": [{"topic": availability_topic}],
            "default_entity_id": f"{component}.{object_id}",
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
        
        if config.is_binary:
            # Binary sensor - hodnoty 0/1
            discovery_payload["payload_on"] = 1
            discovery_payload["payload_off"] = 0
        else:
            # state_class platí pouze pro sensor
            if config.state_class:
                discovery_payload["state_class"] = config.state_class
            # options platí pouze pro sensor (enum)
            if config.options:
                discovery_payload["options"] = config.options
        
        # Společné volitelné atributy
        if config.unit and not config.is_binary:
            discovery_payload["unit_of_measurement"] = config.unit
        if config.device_class:
            discovery_payload["device_class"] = config.device_class
        if config.icon:
            discovery_payload["icon"] = config.icon
        if config.entity_category:
            discovery_payload["entity_category"] = config.entity_category
            
        topic = f"homeassistant/{component}/{unique_id}/config"
        result = self.client.publish(
            topic, json.dumps(discovery_payload), retain=True, qos=self.PUBLISH_QOS
        )
        self.discovery_sent.add(sensor_id)
        logger.debug(
            f"MQTT: Discovery {sensor_id} → {component}/{device_name} (mid={result.mid})"
        )

    def publish_data(self, data: dict[str, Any]) -> bool:
        """Publikuje data na MQTT. Vrací True pokud úspěšně odesláno."""
        if not self.is_ready():
            if self.publish_count == 0 or self.publish_failed % 100 == 0:
                logger.warning("MQTT: Nelze publikovat - není připojeno")
            self.publish_failed += 1
            return False
            
        table = data.get("_table")
        # Připravíme data pro publikování - klíče BEZ prefixu tabulky
        publish_data = {}
        for key in data:
            if key.startswith("_"):
                continue
            cfg, unique_key = get_sensor_config(key, table)
            if cfg:
                self.send_discovery(unique_key, cfg, table)
                value = data[key]
                # Konverze enum hodnot (číslo → text)
                if cfg.options and isinstance(value, int):
                    if 0 <= value < len(cfg.options):
                        value = cfg.options[value]
                # HA `device_class: timestamp` vyžaduje timezone v ISO formátu.
                if cfg.device_class == "timestamp" and isinstance(value, str):
                    raw = value.strip()
                    if raw and not re.search(r"(Z|[+-]\\d{2}:\\d{2})\\s*$", raw):
                        tzinfo = datetime.datetime.now().astimezone().tzinfo or datetime.timezone.utc
                        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f"):
                            try:
                                dt = datetime.datetime.strptime(raw, fmt).replace(tzinfo=tzinfo)
                                value = dt.isoformat()
                                break
                            except ValueError:
                                continue
                # Klíč bez prefixu tabulky (tabulka je v topic)
                publish_data[key] = value
            else:
                # Senzory bez konfigurace - publikujeme pod původním klíčem
                publish_data[key] = data[key]
        
        # Topic specifický pro tabulku
        if table:
            topic = f"{MQTT_NAMESPACE}/{self.device_id}/{table}/state"
        else:
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


class CloudHealthChecker:
    """Sleduje dostupnost OIG cloudu pomocí periodických health checks."""
    
    def __init__(self, server: str, port: int, check_interval: float = 30.0):
        self.server = server
        self.port = port
        self.check_interval = check_interval
        self.is_online = True  # Předpokládáme že cloud je online
        self.last_check_time = 0.0
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self._check_task: asyncio.Task | None = None
        
    async def start(self) -> None:
        """Spustí background task pro health checking."""
        if self._check_task is None:
            self._check_task = asyncio.create_task(self._health_check_loop())
            logger.info(
                f"CloudHealthChecker: Started (interval={self.check_interval}s)"
            )
    
    async def stop(self) -> None:
        """Zastaví health check task."""
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
            self._check_task = None
    
    async def _health_check_loop(self) -> None:
        """Background loop pro periodické health checks."""
        while True:
            try:
                await asyncio.sleep(self.check_interval)
                await self._perform_health_check()
            except asyncio.CancelledError:
                logger.info("CloudHealthChecker: Stopped")
                break
            except Exception as e:
                logger.error(f"CloudHealthChecker: Unexpected error: {e}")
    
    async def _perform_health_check(self) -> None:
        """Provede jeden health check - pokus o TCP connect."""
        self.last_check_time = time.time()
        
        try:
            # Timeout 5s pro connect
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.server, self.port),
                timeout=5.0
            )
            writer.close()
            await writer.wait_closed()
            
            # Úspěch
            self.consecutive_successes += 1
            self.consecutive_failures = 0
            
            if not self.is_online:
                # Recovery ze down stavu
                logger.info(
                    f"CloudHealthChecker: ✅ Cloud RECOVERED "
                    f"({self.consecutive_successes} successful checks)"
                )
                self.is_online = True
            
        except Exception as e:
            # Selhání
            self.consecutive_failures += 1
            self.consecutive_successes = 0
            
            if self.is_online and self.consecutive_failures >= 2:
                # Po 2 selháních deklarujeme outage
                logger.warning(
                    f"CloudHealthChecker: ❌ Cloud DOWN "
                    f"({self.consecutive_failures} failures) - {e}"
                )
                self.is_online = False
            elif not self.is_online:
                logger.debug(
                    f"CloudHealthChecker: Cloud still down "
                    f"({self.consecutive_failures} failures)"
                )


class ACKLearner:
    """Učí se ACK odpovědi z cloudu pro různé typy tabulek."""
    
    # Fallback ACK responses (z databáze analysis)
    DEFAULT_ACK_STANDARD = '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>'
    DEFAULT_ACK_SIMPLE = '<Frame><Result>ACK</Result><CRC>54590</CRC></Frame>'
    DEFAULT_END = '<Frame><Result>END</Result><CRC>34500</CRC></Frame>'
    
    def __init__(self):
        self.learned_acks: dict[str, str] = {}  # table_name -> ACK response
        self.learn_count = 0
    
    def learn_from_cloud(self, cloud_response: str, table_name: str | None) -> None:
        """Zachytí ACK odpověď z cloudu a uloží pro danou tabulku."""
        if not table_name or table_name in self.learned_acks:
            return  # Už máme nebo není co učit
        
        # Detekuj typ ACK odpovědi
        if "<Result>ACK</Result>" in cloud_response:
            self.learned_acks[table_name] = cloud_response
            self.learn_count += 1
            logger.info(
                f"ACKLearner: Learned ACK for {table_name} "
                f"({len(cloud_response)} bytes, total={self.learn_count})"
            )
        elif "<Result>END</Result>" in cloud_response:
            self.learned_acks[table_name] = cloud_response
            logger.info(f"ACKLearner: Learned END for {table_name}")
    
    def generate_ack(self, table_name: str | None) -> str:
        """Vygeneruje ACK odpověď pro danou tabulku."""
        # Pokud máme naučenou odpověď, použij ji
        if table_name and table_name in self.learned_acks:
            return self.learned_acks[table_name]
        
        # Fallback na konstantní ACK
        # IsNewSet polling -> END
        if table_name in ("IsNewSet", "IsNewFW", "IsNewWeather"):
            return self.DEFAULT_END
        
        # Standard telemetry -> ACK
        return self.DEFAULT_ACK_STANDARD


class OfflineQueue:
    """Persistent SQLite fronta pro frames během cloud outage."""
    
    QUEUE_DB_PATH = "/data/offline_queue.db"
    MAX_QUEUE_SIZE = 10000  # Max 10k frames (~80min outage)
    
    def __init__(self):
        self.conn: sqlite3.Connection | None = None
        self._init_db()
    
    def _init_db(self) -> None:
        """Inicializuje SQLite databázi pro frontu."""
        try:
            os.makedirs(os.path.dirname(self.QUEUE_DB_PATH), exist_ok=True)
            self.conn = sqlite3.connect(self.QUEUE_DB_PATH, check_same_thread=False)
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    table_name TEXT NOT NULL,
                    frame_data TEXT NOT NULL,
                    device_id TEXT,
                    queued_at TEXT NOT NULL
                )
                """
            )
            self.conn.commit()
            
            # Zjisti kolik máme frames ve frontě (např. po restartu)
            count = self.size()
            if count > 0:
                logger.info(
                    f"OfflineQueue: Restored {count} queued frames from disk"
                )
        except Exception as e:
            logger.error(f"OfflineQueue: DB init failed: {e}")
            self.conn = None
    
    def add(self, frame_data: str, table_name: str, device_id: str | None = None) -> bool:
        """Přidá frame do fronty."""
        if not self.conn:
            return False
        
        try:
            current_size = self.size()
            if current_size >= self.MAX_QUEUE_SIZE:
                # Fronta plná - zahodíme nejstarší
                self.conn.execute("DELETE FROM queue WHERE id IN (SELECT id FROM queue ORDER BY id LIMIT 1)")
                logger.warning(
                    f"OfflineQueue: Full ({self.MAX_QUEUE_SIZE}) - dropped oldest frame"
                )
            
            self.conn.execute(
                "INSERT INTO queue (timestamp, table_name, frame_data, device_id, queued_at) VALUES (?, ?, ?, ?, ?)",
                (time.time(), table_name, frame_data, device_id, _iso_now())
            )
            self.conn.commit()
            
            new_size = self.size()
            if new_size % 50 == 0:  # Loguj každých 50 frames
                logger.info(f"OfflineQueue: Size={new_size} frames")
            
            return True
        except Exception as e:
            logger.error(f"OfflineQueue: Add failed: {e}")
            return False
    
    def size(self) -> int:
        """Vrátí počet frames ve frontě."""
        if not self.conn:
            return 0
        try:
            cursor = self.conn.execute("SELECT COUNT(*) FROM queue")
            return cursor.fetchone()[0]
        except Exception:
            return 0
    
    def get_batch(self, batch_size: int = 100) -> list[tuple[int, str, str]]:
        """Vrátí batch nejstarších frames (id, table_name, frame_data)."""
        if not self.conn:
            return []
        try:
            cursor = self.conn.execute(
                "SELECT id, table_name, frame_data FROM queue ORDER BY id LIMIT ?",
                (batch_size,)
            )
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"OfflineQueue: Get batch failed: {e}")
            return []
    
    def remove_batch(self, ids: list[int]) -> None:
        """Odstraní frames podle ID po úspěšném odeslání."""
        if not self.conn or not ids:
            return
        try:
            placeholders = ",".join("?" for _ in ids)
            self.conn.execute(f"DELETE FROM queue WHERE id IN ({placeholders})", ids)
            self.conn.commit()
        except Exception as e:
            logger.error(f"OfflineQueue: Remove batch failed: {e}")
    
    def clear(self) -> None:
        """Vymaže celou frontu."""
        if not self.conn:
            return
        try:
            self.conn.execute("DELETE FROM queue")
            self.conn.commit()
            logger.info("OfflineQueue: Cleared")
        except Exception as e:
            logger.error(f"OfflineQueue: Clear failed: {e}")


class OIGProxy:
    def __init__(self) -> None:
        self.parser = OIGDataParser()
        self.mqtt_publisher: MQTTPublisher | None = None
        self.connection_count = 0
        self.device_id: str | None = None
        self.current_state: dict[str, Any] = {}
        self._mqtt_warning_logged = False  # Pro throttling MQTT offline varování
        
        # Cloud health monitoring & offline queue
        self.health_checker = CloudHealthChecker(TARGET_SERVER, TARGET_PORT)
        self.offline_queue = OfflineQueue()
        self.ack_learner = ACKLearner()
        self._replay_task: asyncio.Task | None = None
        self._last_table_name: str | None = None  # Pro tracking ACK learning

    async def handle_connection(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter
    ) -> None:
        self.connection_count += 1
        conn_id = self.connection_count
        client_addr = client_writer.get_extra_info("peername")
        logger.info(f"[#{conn_id}] Nové připojení od {client_addr}")
        
        # Check cloud health
        if self.health_checker.is_online:
            # ONLINE MODE: Standard proxy with learning
            await self._handle_online_mode(
                conn_id, client_reader, client_writer, client_addr
            )
        else:
            # OFFLINE MODE: Local ACK + queueing
            logger.warning(
                f"[#{conn_id}] Cloud OFFLINE - entering offline mode"
            )
            await self._handle_offline_mode(
                conn_id, client_reader, client_writer
            )
    
    async def _handle_online_mode(
        self,
        conn_id: int,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        client_addr: tuple
    ) -> None:
        """Online mode - standardní proxy s ACK learning."""
        tasks: list[asyncio.Task[Any]] = []
        server_writer: asyncio.StreamWriter | None = None
        try:
            server_reader, server_writer = await asyncio.open_connection(
                TARGET_SERVER, TARGET_PORT
            )
            logger.info(
                f"[#{conn_id}] Připojeno k {TARGET_SERVER}:{TARGET_PORT}"
            )
            tasks = [
                asyncio.create_task(
                    self._forward_box_to_cloud(
                        client_reader, server_writer, conn_id, 
                        peer=str(client_addr)
                    )
                ),
                asyncio.create_task(
                    self._forward_cloud_to_box(
                        server_reader, client_writer, conn_id,
                        peer=str(server_writer.get_extra_info("peername")),
                    )
                ),
            ]
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_EXCEPTION
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.gather(*done, return_exceptions=True)
            
            # Po ukončení online mode - check if need to replay queue
            if self.offline_queue.size() > 0:
                logger.info(
                    f"[#{conn_id}] Online session ended, "
                    f"queue has {self.offline_queue.size()} frames"
                )
        except Exception as e:
            logger.error(f"[#{conn_id}] Online mode error: {e}")
            # Přepni do offline mode při chybě
            self.health_checker.is_online = False
            await self._handle_offline_mode(conn_id, client_reader, client_writer)
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
    
    async def _handle_offline_mode(
        self,
        conn_id: int,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter
    ) -> None:
        """Offline mode - lokální ACK generování + queueing."""
        try:
            while True:
                # Čti frame od BOXu
                data = await asyncio.wait_for(
                    client_reader.read(4096), timeout=120.0
                )
                if not data:
                    break
                
                # Zpracuj frame (parsuj, MQTT, atd.)
                text = data.decode("utf-8", errors="ignore")
                parsed = self.parser.parse_xml_frame(text)
                table_name = parsed.get("_table") if parsed else None
                
                # CRITICAL: Pošli ACK OKAMŽITĚ!
                ack_response = self.ack_learner.generate_ack(table_name)
                client_writer.write(ack_response.encode("utf-8"))
                await client_writer.drain()
                
                logger.debug(
                    f"[#{conn_id}] OFFLINE ACK for {table_name}"
                )
                
                # Process frame (MQTT, etc.)
                if parsed and table_name:
                    # Standard processing
                    self._process_data(data, conn_id, None)
                    
                    # Add to queue
                    self.offline_queue.add(text, table_name, self.device_id)
                
                # Check cloud health periodically
                if time.time() - self.health_checker.last_check_time > 30:
                    await self.health_checker._perform_health_check()
                    if self.health_checker.is_online:
                        logger.info(
                            f"[#{conn_id}] Cloud recovered - "
                            "switching to replay mode"
                        )
                        # Trigger replay task
                        if not self._replay_task or self._replay_task.done():
                            self._replay_task = asyncio.create_task(
                                self._replay_queue()
                            )
                        break
        except asyncio.TimeoutError:
            logger.warning(
                f"[#{conn_id}] Offline mode timeout - BOX idle"
            )
        except Exception as e:
            logger.error(f"[#{conn_id}] Offline mode error: {e}")
        finally:
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except Exception:
                pass

    async def _forward_box_to_cloud(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        conn_id: int,
        peer: str | None = None,
    ) -> None:
        """Forward BOX→CLOUD s trackingem tabulek pro ACK learning."""
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                
                # Process and track table name
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
                
                # Forward to cloud
                writer.write(data)
                await writer.drain()
        except Exception as e:
            logger.debug(f"[#{conn_id}] BOX→CLOUD forward ukončen: {e}")
    
    async def _forward_cloud_to_box(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        conn_id: int,
        peer: str | None = None,
    ) -> None:
        """Forward CLOUD→BOX s ACK learning."""
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                
                # Learn ACK responses from cloud
                text = data.decode("utf-8", errors="ignore")
                if "<Result>ACK</Result>" in text or "<Result>END</Result>" in text:
                    # Použij poslední známou tabulku pro learning
                    if self._last_table_name:
                        self.ack_learner.learn_from_cloud(
                            text, self._last_table_name
                        )
                
                # Capture
                capture_payload(
                    self.device_id,
                    None,
                    text,
                    {},
                    direction="proxy_to_box",
                    conn_id=conn_id,
                    peer=peer,
                    length=len(data),
                )
                
                # Forward to BOX
                writer.write(data)
                await writer.drain()
        except Exception as e:
            logger.debug(f"[#{conn_id}] CLOUD→BOX forward ukončen: {e}")
    
    async def _replay_queue(self) -> None:
        """Postupné přehrání fronty na cloud po recovery."""
        queue_size = self.offline_queue.size()
        if queue_size == 0:
            logger.info("Queue replay: No frames to replay")
            return
        
        logger.info(f"Queue replay: Starting - {queue_size} frames")
        replayed_count = 0
        failed_count = 0
        
        try:
            # Open cloud connection
            cloud_reader, cloud_writer = await asyncio.wait_for(
                asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
                timeout=10.0
            )
            
            try:
                # Replay in batches
                while True:
                    batch = self.offline_queue.get_batch(batch_size=50)
                    if not batch:
                        break
                    
                    successfully_sent = []
                    
                    for frame_id, table_name, frame_data in batch:
                        try:
                            # Send frame
                            cloud_writer.write(frame_data.encode("utf-8"))
                            await cloud_writer.drain()
                            
                            # Wait for ACK (timeout 5s)
                            response = await asyncio.wait_for(
                                cloud_reader.read(4096), timeout=5.0
                            )
                            
                            # Verify ACK
                            if b"<Result>ACK</Result>" in response or b"<Result>END</Result>" in response:
                                successfully_sent.append(frame_id)
                                replayed_count += 1
                                logger.debug(
                                    f"Queue replay: {table_name} ACKed "
                                    f"({replayed_count}/{queue_size})"
                                )
                            else:
                                logger.warning(
                                    f"Queue replay: {table_name} - "
                                    "unexpected response"
                                )
                                failed_count += 1
                            
                            # Rate limiting (4-8s delay simulace BOX behavior)
                            await asyncio.sleep(5.0)
                            
                        except asyncio.TimeoutError:
                            logger.error(
                                f"Queue replay: {table_name} - timeout"
                            )
                            failed_count += 1
                        except Exception as e:
                            logger.error(
                                f"Queue replay: {table_name} - {e}"
                            )
                            failed_count += 1
                    
                    # Remove successfully sent frames
                    if successfully_sent:
                        self.offline_queue.remove_batch(successfully_sent)
                        logger.info(
                            f"Queue replay: Removed {len(successfully_sent)} "
                            f"frames, {self.offline_queue.size()} remaining"
                        )
                
                logger.info(
                    f"Queue replay: Complete - "
                    f"{replayed_count} OK, {failed_count} failed"
                )
            finally:
                cloud_writer.close()
                await cloud_writer.wait_closed()
        except Exception as e:
            logger.error(f"Queue replay: Connection failed - {e}")

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
                    
                    # Track table name for ACK learning
                    self._last_table_name = table
                    
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
                    
                    # Měření latence (DT z OIG vs aktuální čas)
                    latency_info = ""
                    if "_dt" in parsed:
                        try:
                            dt = datetime.datetime.strptime(
                                parsed["_dt"], "%Y-%m-%d %H:%M:%S"
                            )
                            now = datetime.datetime.now()
                            latency_sec = (now - dt).total_seconds()
                            latency_info = f" [latence: {latency_sec:.1f}s]"
                        except (ValueError, TypeError):
                            pass
                    
                    # Logování - jen pokud MQTT ready nebo každý 10. frame
                    if self.mqtt_publisher and self.mqtt_publisher.is_ready():
                        logger.info(
                            f"[#{conn_id}] 📊 {table}: "
                            f"{len(parsed)-2} hodnot{latency_info}"
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
                    
                    # Speciální zpracování tbl_events pro MODE tracking
                    if table == "tbl_events" and "Content" in parsed:
                        self._process_mode_event(parsed)
                    
                    # Publikovat pouze pokud je MQTT ready
                    if self.mqtt_publisher and self.mqtt_publisher.is_ready():
                        self.mqtt_publisher.publish_data(parsed)
                        self._mqtt_warning_logged = False
                    return True
        except Exception as e:
            logger.error(f"[#{conn_id}] Chyba parsování: {e}")
        return False

    def _process_mode_event(self, parsed: dict[str, Any]) -> None:
        """Zpracuje tbl_events frame a detekuje změnu MODE."""
        global _current_mode
        
        content = parsed.get("Content", "")
        if "tbl_box_prms" in content and "MODE:" in content:
            new_mode = _parse_mode_from_event(content)
            if new_mode is not None and new_mode != _current_mode:
                _current_mode = new_mode
                _save_mode_state(new_mode)
                
                # Publikovat virtuální senzor tbl_box_prms:MODE
                if self.mqtt_publisher and self.mqtt_publisher.is_ready():
                    mode_data = {
                        "_table": "tbl_box_prms",
                        "_device_id": parsed.get("_device_id"),
                        "MODE": new_mode
                    }
                    logger.info(f"MODE: Publikuji virtuální senzor: MODE={new_mode}")
                    self.mqtt_publisher.publish_data(mode_data)
    
    def _init_mqtt(self, device_id: str) -> None:
        """Inicializuje MQTT publisher a spustí health check."""
        global _current_mode
        
        logger.info(f"MQTT: Inicializuji pro device {device_id}")
        self.mqtt_publisher = MQTTPublisher(device_id)
        
        # Načíst uložený MODE stav
        _current_mode = _load_mode_state()
        if _current_mode is not None:
            logger.info(f"MODE: Obnovuji stav z úložiště: {_current_mode}")
        
        if self.mqtt_publisher.connect():
            logger.info("MQTT: ✅ Počáteční připojení úspěšné")
            
            # Publikovat MODE senzor pokud máme uložený stav
            if _current_mode is not None:
                mode_data = {
                    "_table": "tbl_box_prms",
                    "_device_id": device_id,
                    "MODE": _current_mode
                }
                logger.info(f"MODE: Publikuji obnovený stav při startu: MODE={_current_mode}")
                self.mqtt_publisher.publish_data(mode_data)
            
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
        
        # Start cloud health checker
        await self.health_checker.start()
        logger.info("🏥 CloudHealthChecker started")
        
        # Check if we have queued frames from previous run
        queue_size = self.offline_queue.size()
        if queue_size > 0:
            logger.warning(
                f"📦 Found {queue_size} queued frames from previous run"
            )
        
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
