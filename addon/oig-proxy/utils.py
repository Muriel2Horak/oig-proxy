#!/usr/bin/env python3
"""
Pomocné funkce pro OIG Proxy.
"""

import base64
import datetime
import ipaddress
import json
import logging
import os
import queue
import sqlite3
import threading
import time
from contextlib import suppress
from types import ModuleType
from typing import Any

from config import (
    CAPTURE_DB_PATH,
    CAPTURE_PAYLOADS,
    CAPTURE_RAW_BYTES,
    CAPTURE_RETENTION_DAYS,
    MAP_RELOAD_SECONDS,
    MODE_STATE_PATH,
    PRMS_STATE_PATH,
    SENSOR_MAP_PATH,
)
from models import SensorConfig

logger = logging.getLogger(__name__)

# Public DNS resolver for cloud target (bypass local override)
dns_resolver: ModuleType | None
try:
    import dns.resolver as dns_resolver  # type: ignore
except ImportError:  # pragma: no cover - optional dependency guard
    dns_resolver = None

dns: ModuleType | None = dns_resolver  # pylint: disable=invalid-name

_PUBLIC_DNS_HOSTS = {"oigservis.cz"}
_PUBLIC_DNS_DEFAULT = ("8.8.8.8", "1.1.1.1")
_PUBLIC_DNS_CACHE: dict[str, tuple[str, float]] = {}
_PUBLIC_DNS_LAST_LOG: dict[str, str] = {}
_PUBLIC_DNS_TTL_DEFAULT_S = 300.0
_PUBLIC_DNS_TTL_MIN_S = 30.0
_PUBLIC_DNS_TTL_MAX_S = 3600.0

# Globální state
SENSORS: dict[str, SensorConfig] = {}
WARNING_MAP: dict[str, list[dict[str, Any]]] = {}
_last_map_load = 0.0  # pylint: disable=invalid-name
_capture_queue: queue.Queue[tuple[Any, ...]] | None = None  # pylint: disable=invalid-name
_capture_thread: threading.Thread | None = None  # pylint: disable=invalid-name
_capture_cols: set[str] = set()  # pylint: disable=invalid-name


def iso_now() -> str:
    """Vrátí aktuální čas v ISO formátu."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def friendly_name(sensor_id: str) -> str:
    """Vytvoří lidsky čitelný název ze sensor_id."""
    parts = sensor_id.replace("_", " ").split()
    return " ".join(p.capitalize() for p in parts)


def _normalize_hostname(host: str) -> str:
    return host.strip().rstrip(".").lower()


def _is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _public_dns_nameservers() -> list[str]:
    raw = os.getenv("CLOUD_PUBLIC_DNS", "").strip()
    if raw:
        servers = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        servers = list(_PUBLIC_DNS_DEFAULT)
    # Keep only IPv4 addresses
    valid = [s for s in servers if _is_ip_address(s)]
    return valid or list(_PUBLIC_DNS_DEFAULT)


def _public_dns_cache_get(host: str) -> str | None:
    cached = _PUBLIC_DNS_CACHE.get(host)
    if not cached:
        return None
    ip, expires_at = cached
    if expires_at <= time.time():
        _PUBLIC_DNS_CACHE.pop(host, None)
        return None
    return ip


def _public_dns_cache_set(host: str, ip: str, ttl_s: float) -> None:
    ttl = max(_PUBLIC_DNS_TTL_MIN_S, min(_PUBLIC_DNS_TTL_MAX_S, ttl_s))
    _PUBLIC_DNS_CACHE[host] = (ip, time.time() + ttl)


def _resolve_public_dns(host: str) -> tuple[str | None, float]:
    if dns is None:
        return None, _PUBLIC_DNS_TTL_DEFAULT_S
    # Determine the correct Resolver class depending on what `dns` refers to:
    # - if `dns` exposes Resolver directly, use it
    # - otherwise fallback to `dns.resolver.Resolver`
    resolver_cls = getattr(dns, "Resolver", None)
    if resolver_cls is None:
        nested = getattr(dns, "resolver", None)
        resolver_cls = getattr(nested, "Resolver", None) if nested else None
    if resolver_cls is None:
        logger.warning(
            "Public DNS resolution unavailable: dnspython Resolver not found"
        )
        return None, _PUBLIC_DNS_TTL_DEFAULT_S
    resolver = resolver_cls(configure=False)
    resolver.nameservers = _public_dns_nameservers()
    try:
        answer = resolver.resolve(host, "A", lifetime=2.0)
        ip = str(answer[0])
        ttl = float(getattr(answer.rrset, "ttl", _PUBLIC_DNS_TTL_DEFAULT_S))
        return ip, ttl
    except Exception:  # pylint: disable=broad-exception-caught
        return None, _PUBLIC_DNS_TTL_DEFAULT_S


def resolve_cloud_host(host: str) -> str:
    """
    Resolve cloud host using public DNS to bypass local overrides.
    Only applies to known cloud domains (e.g. oigservis.cz).
    """
    if not host:
        return host
    normalized = _normalize_hostname(host)
    if _is_ip_address(normalized):
        return normalized
    if normalized not in _PUBLIC_DNS_HOSTS:
        return host

    cached = _public_dns_cache_get(normalized)
    if cached:
        return cached

    ip, ttl = _resolve_public_dns(normalized)
    if ip:
        _public_dns_cache_set(normalized, ip, ttl)
        if _PUBLIC_DNS_LAST_LOG.get(normalized) != ip:
            logger.info("☁️ Cloud DNS (public): %s -> %s", normalized, ip)
            _PUBLIC_DNS_LAST_LOG[normalized] = ip
        return ip

    raise RuntimeError(f"Public DNS resolution failed for {normalized}")


def load_mode_state() -> tuple[int | None, str | None]:
    """Načte uložený MODE stav z perzistentního souboru.

    Returns:
        (mode_value, device_id) – device_id může být None pokud nebyl uložen.
    """
    try:
        if os.path.exists(MODE_STATE_PATH):
            with open(MODE_STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                mode_value = data.get("mode")
                device_id = data.get("device_id")
                if mode_value is not None:
                    try:
                        mode_int = int(mode_value)
                    except (TypeError, ValueError):
                        mode_int = None
                    if mode_int is None or mode_int < 0 or mode_int > 5:
                        logger.warning(
                            "MODE: Stored value %s is out of range 0-5, ignoring", mode_value, )
                        return None, device_id
                    logger.info(
                        "MODE: Loaded saved state: %s (device_id=%s)",
                        mode_int,
                        device_id,
                    )
                    return mode_int, device_id
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("MODE: Failed to load state: %s", exc)
    return None, None


def save_mode_state(mode_value: int, device_id: str | None) -> None:
    """Uloží MODE stav do perzistentního souboru."""
    try:
        os.makedirs(os.path.dirname(MODE_STATE_PATH), exist_ok=True)
        with open(MODE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "mode": mode_value,
                "device_id": device_id,
                "timestamp": iso_now()
            }, f, ensure_ascii=False)
        logger.debug(
            "MODE: State saved: %s (device_id=%s)",
            mode_value,
            device_id,
        )
    except (OSError, TypeError, ValueError) as exc:
        logger.error("MODE: Failed to save state: %s", exc)


def _load_json_file(path: str) -> Any | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
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


def _split_prms_state(
        data: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str | None]:
    device_id = data.get("device_id")
    raw_tables = data.get("tables")

    # Backward compatibility: pokud je soubor přímo dict table->values
    if raw_tables is None:
        raw_tables = data
        device_id = None

    return _parse_prms_tables(raw_tables), (str(
        device_id) if device_id else None)


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
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("STATE: Failed to load table state: %s", exc)
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
        existing_dict: dict[str, Any] = existing if isinstance(
            existing, dict) else {}

        existing_device_id = existing_dict.get("device_id")
        existing_tables = existing_dict.get("tables")

        # Backward compatibility: pokud je soubor přímo dict table->values
        if existing_tables is None and any(isinstance(k, str) and k.startswith(
                "tbl_") for k in existing_dict.keys()):
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
    except (OSError, TypeError, ValueError) as exc:
        logger.debug(
            "STATE: Failed to save table state (%s): %s",
            table_name,
            exc,
        )


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
    except (TypeError, ValueError):
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
            "Control - fronta (počet)",
            "",
            None,
            "measurement",
            None,
            "proxy",
            "diagnostic",
            None,
            False,
            "state",
        ),
        "proxy_status:control_inflight": SensorConfig(
            "Control - běžící příkaz", "", None, None, None, "proxy", "diagnostic"
        ),
        "proxy_status:control_last_result": SensorConfig(
            "Control - poslední výsledek", "", None, None, None, "proxy", "diagnostic"
        ),
    }


def _add_sensors_from_mapping(mapping: dict[str, Any]) -> int:  # pylint: disable=too-many-locals
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


def _build_warning_map(
        mapping: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
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
    global _last_map_load, WARNING_MAP  # pylint: disable=global-statement

    now = time.time()
    if MAP_RELOAD_SECONDS > 0 and (now - _last_map_load) < MAP_RELOAD_SECONDS:
        return

    if not os.path.exists(SENSOR_MAP_PATH):
        logger.info("JSON mapping not found, skipped (%s)", SENSOR_MAP_PATH)
        return

    try:
        loaded = _load_json_file(SENSOR_MAP_PATH)
        if not isinstance(loaded, dict):
            return

        added = _add_sensors_from_mapping(loaded)
        if added:
            logger.info(
                "Sensor map: Loaded %s sensors from %s",
                added,
                SENSOR_MAP_PATH,
            )
            sample = list(SENSORS.keys())[:5]
            logger.debug("Sensor map sample: %s", sample)

        WARNING_MAP = _build_warning_map(loaded)
        _last_map_load = now
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Sensor map load failed: %s", exc)


def init_capture_db() -> tuple[sqlite3.Connection | None, set[str]]:
    """Inicializuje SQLite DB pro capture."""
    if not CAPTURE_PAYLOADS:
        return None, set()

    try:
        conn = sqlite3.connect(CAPTURE_DB_PATH, check_same_thread=False)
        # PRAGMA pro lepší výkon a menší blokování (writer poběží v background
        # threadu)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA busy_timeout=2000")
        except sqlite3.Error as exc:
            logger.debug("Capture DB pragma setup failed: %s", exc)

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
            except sqlite3.Error as exc:
                logger.debug(
                    "Capture DB column %s add skipped: %s", col_name, exc)

        cols = {row[1] for row in conn.execute("PRAGMA table_info(frames)")}
        return conn, cols
    except (sqlite3.Error, OSError) as exc:
        logger.warning("Init capture DB failed: %s", exc)
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
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Capture worker write failed (dropping batch): %s", exc)
        with suppress(Exception):
            conn.rollback()


def _capture_loop(conn: sqlite3.Connection, sql: str,
                  q: queue.Queue[tuple[Any, ...]]) -> None:
    batch: list[tuple[Any, ...]] = []
    last_commit = time.time()
    last_prune = 0.0
    while True:
        try:
            item = q.get(timeout=1.0)
        except queue.Empty:
            item = None

        if item is None:
            _commit_capture_batch(conn, sql, batch)
            batch.clear()
            last_commit = time.time()
            if CAPTURE_RETENTION_DAYS > 0 and (time.time() - last_prune) >= 600:
                _prune_capture_db(conn)
                last_prune = time.time()
            continue

        batch.append(item)
        if len(batch) >= 200 or (time.time() - last_commit) >= 0.5:
            _commit_capture_batch(conn, sql, batch)
            batch.clear()
            last_commit = time.time()
        if CAPTURE_RETENTION_DAYS > 0 and (time.time() - last_prune) >= 600:
            _prune_capture_db(conn)
            last_prune = time.time()


def _prune_capture_db(conn: sqlite3.Connection) -> None:
    """Prune capture DB to keep it bounded."""
    if CAPTURE_RETENTION_DAYS <= 0:
        return
    try:
        cutoff = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=CAPTURE_RETENTION_DAYS)
        )
        cutoff_iso = cutoff.replace(microsecond=0).isoformat()
        cur = conn.execute("DELETE FROM frames WHERE ts < ?", (cutoff_iso,))
        deleted = cur.rowcount if cur.rowcount is not None else 0
        conn.commit()
        if deleted:
            with suppress(Exception):
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Capture DB prune failed: %s", exc)


def _capture_worker(db_path: str) -> None:
    """Background writer pro payload capture (neblokuje asyncio event loop)."""
    try:
        conn = sqlite3.connect(db_path)
        _configure_capture_conn(conn)

        sql = (
            "INSERT INTO frames "
            "(ts, device_id, table_name, raw, raw_b64, parsed, direction, conn_id, peer, length) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)")

        if _capture_queue is None:
            logger.warning("Capture worker started without queue")
            return
        _capture_loop(conn, sql, _capture_queue)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("Capture worker crashed: %s", exc)


def capture_payload(  # pylint: disable=too-many-arguments,too-many-positional-arguments,global-statement
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
        except sqlite3.Error as exc:
            logger.debug("Capture DB close failed: %s", exc)
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
        if _capture_queue is None:
            logger.debug("Capture queue missing; dropping payload")
            return
        try:
            _capture_queue.put_nowait(values)
        except queue.Full:
            logger.debug("Capture queue full - dropping payload")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Capture payload failed: %s", exc)


# Initialize capture DB at module load
_conn, _capture_cols = init_capture_db()
try:
    if _conn is not None:
        _conn.close()
except sqlite3.Error as exc:
    logger.debug("Capture DB cleanup failed: %s", exc)
