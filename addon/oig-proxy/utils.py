#!/usr/bin/env python3
"""
Pomocné funkce pro OIG Proxy.
"""

import datetime
import json
import logging
import os
import queue
import sqlite3
import threading
import time
import base64
from contextlib import suppress
from typing import Any

from config import (
    CAPTURE_DB_PATH,
    CAPTURE_PAYLOADS,
    CAPTURE_RAW_BYTES,
    MAP_RELOAD_SECONDS,
    MODE_STATE_PATH,
    PRMS_STATE_PATH,
    SENSOR_MAP_PATH,
)
from models import SensorConfig, WarningEntry

logger = logging.getLogger(__name__)

# Globální state
SENSORS: dict[str, SensorConfig] = {}
WARNING_MAP: dict[str, list[dict[str, Any]]] = {}
_last_map_load = 0.0
_capture_queue: queue.Queue[tuple[Any, ...]] | None = None
_capture_thread: threading.Thread | None = None
_capture_cols: set[str] = set()


def iso_now() -> str:
    """Vrátí aktuální čas v ISO formátu."""
    return datetime.datetime.now(datetime.UTC).isoformat()


def friendly_name(sensor_id: str) -> str:
    """Vytvoří lidsky čitelný název ze sensor_id."""
    parts = sensor_id.replace("_", " ").split()
    return " ".join(p.capitalize() for p in parts)


def load_mode_state() -> tuple[int | None, str | None]:
    """Načte uložený MODE stav z perzistentního souboru.
    
    Returns:
        (mode_value, device_id) – device_id může být None pokud nebyl uložen.
    """
    try:
        if os.path.exists(MODE_STATE_PATH):
            with open(MODE_STATE_PATH, "r") as f:
                data = json.load(f)
                mode_value = data.get("mode")
                device_id = data.get("device_id")
                if mode_value is not None:
                    try:
                        mode_int = int(mode_value)
                    except Exception:
                        mode_int = None
                    if mode_int is None or mode_int < 0 or mode_int > 5:
                        logger.warning(
                            f"MODE: Uložená hodnota {mode_value} je mimo rozsah 0-5, ignoruji"
                        )
                        return None, device_id
                    logger.info(
                        f"MODE: Načten uložený stav: {mode_int} "
                        f"(device_id={device_id})"
                    )
                    return mode_int, device_id
    except Exception as e:
        logger.warning(f"MODE: Nepodařilo se načíst stav: {e}")
    return None, None


def save_mode_state(mode_value: int, device_id: str | None) -> None:
    """Uloží MODE stav do perzistentního souboru."""
    try:
        os.makedirs(os.path.dirname(MODE_STATE_PATH), exist_ok=True)
        with open(MODE_STATE_PATH, "w") as f:
            json.dump({
                "mode": mode_value,
                "device_id": device_id,
                "timestamp": iso_now()
            }, f)
        logger.debug(
            f"MODE: Stav uložen: {mode_value} (device_id={device_id})"
        )
    except Exception as e:
        logger.error(f"MODE: Nepodařilo se uložit stav: {e}")


def _load_json_file(path: str) -> Any | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _parse_prms_tables(raw_tables: Any) -> dict[str, dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_tables, dict):
        return tables

    for table_name, entry in raw_tables.items():
        if not isinstance(table_name, str) or not isinstance(entry, dict):
            continue
        if "values" in entry:
            values = entry.get("values")
            if isinstance(values, dict):
                tables[table_name] = values
            continue
        tables[table_name] = entry
    return tables


def _split_prms_state(data: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str | None]:
    device_id = data.get("device_id")
    raw_tables = data.get("tables")

    # Backward compatibility: pokud je soubor přímo dict table->values
    if raw_tables is None:
        raw_tables = data
        device_id = None

    return _parse_prms_tables(raw_tables), (str(device_id) if device_id else None)


def load_prms_state() -> tuple[dict[str, dict[str, Any]], str | None]:
    """Načte poslední známé hodnoty tabulek z perzistentního souboru.

    Returns:
        (tables, device_id) – tables mapuje table_name -> hodnoty (bez _ klíčů).
    """
    try:
        if not os.path.exists(PRMS_STATE_PATH):
            return {}, None
        loaded = _load_json_file(PRMS_STATE_PATH)
        if not isinstance(loaded, dict):
            return {}, None
        return _split_prms_state(loaded)
    except Exception as e:
        logger.warning(f"STATE: Nepodařilo se načíst table state: {e}")
        return {}, None


def save_prms_state(
    table_name: str,
    values: dict[str, Any],
    device_id: str | None,
) -> None:
    """Uloží/aktualizuje poslední známé hodnoty pro danou tabulku."""
    if not table_name:
        return
    if not isinstance(values, dict) or not values:
        return

    try:
        os.makedirs(os.path.dirname(PRMS_STATE_PATH), exist_ok=True)

        existing = _load_json_file(PRMS_STATE_PATH)
        existing_dict: dict[str, Any] = existing if isinstance(existing, dict) else {}

        existing_device_id = existing_dict.get("device_id")
        existing_tables = existing_dict.get("tables")

        # Backward compatibility: pokud je soubor přímo dict table->values
        if existing_tables is None and any(
            isinstance(k, str) and k.startswith("tbl_") for k in existing_dict.keys()
        ):
            existing_tables = existing_dict
            existing_device_id = None

        existing_tables_dict: dict[str, Any] = (
            existing_tables if isinstance(existing_tables, dict) else {}
        )

        prior_entry = existing_tables_dict.get(table_name)
        prior_values: dict[str, Any] = {}
        if isinstance(prior_entry, dict) and "values" in prior_entry:
            pv = prior_entry.get("values")
            if isinstance(pv, dict):
                prior_values = pv
        elif isinstance(prior_entry, dict):
            prior_values = prior_entry

        merged = dict(prior_values)
        merged.update(values)
        existing_tables_dict[table_name] = {"ts": iso_now(), "values": merged}

        out = {
            "device_id": str(device_id) if device_id else existing_device_id,
            "tables": existing_tables_dict,
        }
        with open(PRMS_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
    except Exception as e:
        logger.debug(f"STATE: Nepodařilo se uložit table state ({table_name}): {e}")


def get_sensor_config(
    sensor_id: str,
    table: str | None = None
) -> tuple[SensorConfig | None, str]:
    """Vrátí konfiguraci senzoru a jeho unikátní klíč.
    
    Pořadí vyhledávání:
    1. table:sensor_id (specifické mapování pro tabulku)
    2. sensor_id (obecné mapování, fallback)
    
    Returns:
        tuple: (SensorConfig nebo None, unikátní klíč pro senzor)
    """
    if table:
        table_key = f"{table}:{sensor_id}"
        config = SENSORS.get(table_key)
        if config:
            return config, table_key
    
    config = SENSORS.get(sensor_id)
    if config:
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
        if (val_int & int(bit)) and remark:
            texts.append(remark)
    return texts


def _builtin_sensors() -> dict[str, SensorConfig]:
    return {
        "tbl_events:Type": SensorConfig(
            "Typ události", "", None, None, None, "proxy", "diagnostic"
        ),
        "tbl_events:Confirm": SensorConfig(
            "Potvrzení události", "", None, None, None, "proxy", "diagnostic"
        ),
        "tbl_events:Content": SensorConfig(
            "Text události", "", None, None, None, "proxy", "diagnostic"
        ),
        "proxy_status:status": SensorConfig(
            "Stav komunikace", "", None, None, None, "proxy", "diagnostic"
        ),
        "proxy_status:mode": SensorConfig(
            "Režim komunikace", "", None, None, None, "proxy", "diagnostic"
        ),
        "proxy_status:last_data": SensorConfig(
            "Poslední data", "", "timestamp", None, None, "proxy", "diagnostic"
        ),
        "proxy_status:cloud_online": SensorConfig(
            "Cloud připojen", "", "connectivity", None, None, "proxy", "diagnostic", None, True
        ),
        "proxy_status:cloud_session_connected": SensorConfig(
            "Cloud TCP připojen", "", "connectivity", None, None, "proxy", "diagnostic", None, True
        ),
        "proxy_status:cloud_session_active": SensorConfig(
            "Cloud TCP aktivní spojení", "", None, "measurement", None, "proxy", "diagnostic"
        ),
        "proxy_status:cloud_connects": SensorConfig(
            "Cloud - připojení (počet)", "", None, "measurement", None, "proxy", "diagnostic"
        ),
        "proxy_status:cloud_disconnects": SensorConfig(
            "Cloud - odpojení (počet)", "", None, "measurement", None, "proxy", "diagnostic"
        ),
        "proxy_status:cloud_timeouts": SensorConfig(
            "Cloud - timeouts (počet)", "", None, "measurement", None, "proxy", "diagnostic"
        ),
        "proxy_status:cloud_errors": SensorConfig(
            "Cloud - chyby (počet)", "", None, "measurement", None, "proxy", "diagnostic"
        ),
        "proxy_status:box_connected": SensorConfig(
            "BOX připojen", "", "connectivity", None, None, "proxy", "diagnostic", None, True
        ),
        "proxy_status:box_data_recent": SensorConfig(
            "Data z BOXu tečou", "", "connectivity", None, None, "proxy", "diagnostic", None, True
        ),
        "proxy_status:box_connections": SensorConfig(
            "BOX spojení (počet)", "", None, "measurement", None, "proxy", "diagnostic"
        ),
        "proxy_status:box_connections_active": SensorConfig(
            "BOX aktivní spojení", "", None, "measurement", None, "proxy", "diagnostic"
        ),
        "proxy_status:cloud_queue": SensorConfig(
            "Cloud fronta", "", None, "measurement", None, "proxy", "diagnostic"
        ),
        "proxy_status:mqtt_queue": SensorConfig(
            "MQTT fronta", "", None, "measurement", None, "proxy", "diagnostic"
        ),
        "proxy_status:isnewset_polls": SensorConfig(
            "IsNewSet - počet", "", None, "measurement", None, "proxy", "diagnostic"
        ),
        "proxy_status:isnewset_last_poll": SensorConfig(
            "IsNewSet - poslední dotaz", "", "timestamp", None, None, "proxy", "diagnostic"
        ),
        "proxy_status:isnewset_last_response": SensorConfig(
            "IsNewSet - poslední odpověď", "", None, None, None, "proxy", "diagnostic"
        ),
        "proxy_status:isnewset_last_rtt_ms": SensorConfig(
            "IsNewSet - RTT", "ms", None, "measurement", None, "proxy", "diagnostic"
        ),
        "proxy_status:control_queue_len": SensorConfig(
            "Control - fronta (počet)", "", None, "measurement", None, "proxy", "diagnostic", None, False, "state"
        ),
        "proxy_status:control_inflight": SensorConfig(
            "Control - běžící příkaz", "", None, None, None, "proxy", "diagnostic"
        ),
        "proxy_status:control_last_result": SensorConfig(
            "Control - poslední výsledek", "", None, None, None, "proxy", "diagnostic"
        ),
    }


def _add_sensors_from_mapping(mapping: dict[str, Any]) -> int:
    sensors = mapping.get("sensors", {})
    if not isinstance(sensors, dict):
        return 0

    added = 0
    for sid, meta in sensors.items():
        if not isinstance(sid, str) or not isinstance(meta, dict):
            continue
        name = meta.get("name_cs") or meta.get("name") or friendly_name(sid)
        unit = meta.get("unit_of_measurement") or ""
        device_class = meta.get("device_class")
        state_class = meta.get("state_class")
        icon = meta.get("icon")
        device_mapping = meta.get("device_mapping")
        entity_category = meta.get("entity_category")
        options = meta.get("options")
        is_binary = meta.get("is_binary", False)
        json_attributes_topic = meta.get("json_attributes_topic")

        SENSORS[sid] = SensorConfig(
            name,
            unit,
            device_class,
            state_class,
            icon,
            device_mapping,
            entity_category,
            options,
            is_binary,
            json_attributes_topic,
        )
        added += 1

    for sid, cfg in _builtin_sensors().items():
        if sid not in SENSORS:
            SENSORS[sid] = cfg
            added += 1

    return added


def _build_warning_map(mapping: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    entries = mapping.get("warnings_3f", [])
    if not isinstance(entries, list):
        return out

    for w in entries:
        if not isinstance(w, dict):
            continue
        key = w.get("table_key") or w.get("key")
        bit = w.get("bit")
        remark = w.get("remark")
        remark_cs = w.get("remark_cs")
        code = w.get("warning_code")
        if not key or bit is None:
            continue
        out.setdefault(str(key), []).append(
            {
                "bit": int(bit),
                "remark": remark,
                "remark_cs": remark_cs,
                "code": code,
            }
        )
    return out


def load_sensor_map() -> None:
    """Načte mapping z JSON (vygenerovaný z Excelu) a doplní SENSORS."""
    global _last_map_load, WARNING_MAP
    
    now = time.time()
    if MAP_RELOAD_SECONDS > 0 and (now - _last_map_load) < MAP_RELOAD_SECONDS:
        return
    
    if not os.path.exists(SENSOR_MAP_PATH):
        logger.info(
            f"JSON mapping nenalezen, přeskočeno ({SENSOR_MAP_PATH})"
        )
        return
    
    try:
        loaded = _load_json_file(SENSOR_MAP_PATH)
        if not isinstance(loaded, dict):
            return

        added = _add_sensors_from_mapping(loaded)
        if added:
            logger.info(f"Sensor map: Načteno {added} senzorů z {SENSOR_MAP_PATH}")
            sample = list(SENSORS.keys())[:5]
            logger.debug(f"Sensor map sample: {sample}")

        WARNING_MAP = _build_warning_map(loaded)
        _last_map_load = now
    except Exception as e:
        logger.warning(f"Načtení mappingu selhalo: {e}")


def init_capture_db() -> tuple[sqlite3.Connection | None, set[str]]:
    """Inicializuje SQLite DB pro capture."""
    if not CAPTURE_PAYLOADS:
        return None, set()
    
    try:
        conn = sqlite3.connect(CAPTURE_DB_PATH, check_same_thread=False)
        # PRAGMA pro lepší výkon a menší blokování (writer poběží v background threadu)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA busy_timeout=2000")
        except Exception:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                device_id TEXT,
                table_name TEXT,
                raw TEXT,
                raw_b64 TEXT,
                parsed TEXT,
                direction TEXT,
                conn_id INTEGER,
                peer TEXT,
                length INTEGER
            )
        """)

        # Přidat chybějící sloupce (backward compatibility)
        for col_name, col_type in [
            ("raw_b64", "TEXT"),
            ("direction", "TEXT"),
            ("conn_id", "INTEGER"),
            ("peer", "TEXT"),
            ("length", "INTEGER"),
        ]:
            try:
                conn.execute(
                    f"ALTER TABLE frames ADD COLUMN {col_name} {col_type}"
                )
                conn.commit()
            except Exception:
                pass
        
        cols = {row[1] for row in conn.execute("PRAGMA table_info(frames)")}
        return conn, cols
    except Exception as e:
        logger.warning(f"Init capture DB failed: {e}")
        return None, set()


def _configure_capture_conn(conn: sqlite3.Connection) -> None:
    with suppress(Exception):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA busy_timeout=2000")


def _commit_capture_batch(
    conn: sqlite3.Connection, sql: str, batch: list[tuple[Any, ...]]
) -> None:
    if not batch:
        return
    try:
        conn.executemany(sql, batch)
        conn.commit()
    except Exception as e:
        logger.debug(f"Capture worker write failed (dropping batch): {e}")
        with suppress(Exception):
            conn.rollback()


def _capture_loop(conn: sqlite3.Connection, sql: str, q: queue.Queue[tuple[Any, ...]]) -> None:
    batch: list[tuple[Any, ...]] = []
    last_commit = time.time()
    while True:
        try:
            item = q.get(timeout=1.0)
        except queue.Empty:
            item = None

        if item is None:
            _commit_capture_batch(conn, sql, batch)
            batch.clear()
            last_commit = time.time()
            continue

        batch.append(item)
        if len(batch) >= 200 or (time.time() - last_commit) >= 0.5:
            _commit_capture_batch(conn, sql, batch)
            batch.clear()
            last_commit = time.time()


def _capture_worker(db_path: str) -> None:
    """Background writer pro payload capture (neblokuje asyncio event loop)."""
    try:
        conn = sqlite3.connect(db_path)
        _configure_capture_conn(conn)

        sql = (
            "INSERT INTO frames "
            "(ts, device_id, table_name, raw, raw_b64, parsed, direction, conn_id, peer, length) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)"
        )

        assert _capture_queue is not None
        _capture_loop(conn, sql, _capture_queue)
    except Exception as e:
        logger.warning(f"Capture worker crashed: {e}")


def capture_payload(
    device_id: str | None,
    table: str | None,
    raw: str,
    raw_bytes: bytes | None,
    parsed: dict[str, Any],
    direction: str | None = None,
    conn_id: int | None = None,
    peer: str | None = None,
    length: int | None = None,
) -> None:
    """Uloží payload do capture databáze."""
    global _capture_queue, _capture_thread, _capture_cols

    if not CAPTURE_PAYLOADS:
        return

    # Lazy init thread/queue (po startu procesu)
    if _capture_queue is None or _capture_thread is None or not _capture_thread.is_alive():
        # Zajistíme schema + sloupce (backward compatibility)
        conn, cols = init_capture_db()
        if conn is None:
            return
        _capture_cols = cols
        try:
            conn.close()
        except Exception:
            pass
        _capture_queue = queue.Queue(maxsize=5000)
        _capture_thread = threading.Thread(
            target=_capture_worker,
            args=(CAPTURE_DB_PATH,),
            daemon=True,
            name="capture-writer",
        )
        _capture_thread.start()

    try:
        ts = iso_now()
        raw_b64: str | None = None
        if CAPTURE_RAW_BYTES and raw_bytes is not None:
            raw_b64 = base64.b64encode(raw_bytes).decode("ascii")
        values = (
            ts,
            device_id,
            table,
            raw,
            raw_b64,
            json.dumps(parsed, ensure_ascii=False),
            direction,
            conn_id,
            peer,
            length,
        )
        assert _capture_queue is not None
        try:
            _capture_queue.put_nowait(values)
        except queue.Full:
            logger.debug("Capture queue full - dropping payload")
    except Exception as e:
        logger.debug(f"Capture payload failed: {e}")


# Initialize capture DB at module load
_conn, _capture_cols = init_capture_db()
try:
    if _conn is not None:
        _conn.close()
except Exception:
    pass
