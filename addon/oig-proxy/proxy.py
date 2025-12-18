#!/usr/bin/env python3
"""
OIG Proxy - hlavní orchestrace s ONLINE/OFFLINE/REPLAY režimy.
"""

import asyncio
import logging
import socket
import time
import random
import json
from collections import deque
from contextlib import suppress
import re
from datetime import datetime, timezone
from typing import Any

from cloud_manager import ACKLearner, CloudHealthChecker, CloudQueue
from config import (
    CLOUD_REPLAY_RATE,
    CLOUD_ACK_TIMEOUT,
    CONTROL_API_HOST,
    CONTROL_API_PORT,
    CONTROL_MQTT_ACK_TIMEOUT_S,
    CONTROL_MQTT_APPLIED_TIMEOUT_S,
    CONTROL_MQTT_BOX_READY_SECONDS,
    CONTROL_MQTT_ENABLED,
    CONTROL_MQTT_MODE_QUIET_SECONDS,
    CONTROL_MQTT_QOS,
    CONTROL_MQTT_RETAIN,
    CONTROL_MQTT_RESULT_TOPIC,
    CONTROL_MQTT_SET_TOPIC,
    CONTROL_WRITE_WHITELIST,
    PROXY_LISTEN_HOST,
    PROXY_LISTEN_PORT,
    PROXY_STATUS_INTERVAL,
    REPLAY_ACK_TIMEOUT,
    TARGET_PORT,
    TARGET_SERVER,
)
from control_api import ControlAPIServer
from local_oig_crc import build_frame
from models import ProxyMode
from mqtt_publisher import MQTTPublisher
from parser import OIGDataParser
from utils import (
    capture_payload,
    load_mode_state,
    load_prms_state,
    save_mode_state,
    save_prms_state,
)

logger = logging.getLogger(__name__)


# ============================================================================
# OIG Proxy - hlavní proxy server
# ============================================================================

class OIGProxy:
    """OIG Proxy s podporou ONLINE/OFFLINE/REPLAY režimů."""
    
    def __init__(self, device_id: str):
        self.device_id = device_id
        
        # Komponenty
        self.cloud_queue = CloudQueue()
        self.cloud_health = CloudHealthChecker(TARGET_SERVER, TARGET_PORT)
        self.ack_learner = ACKLearner()
        self.mqtt_publisher = MQTTPublisher(device_id)
        self.parser = OIGDataParser()
        loaded_mode, loaded_dev = load_mode_state()
        self._mode_value: int | None = loaded_mode
        self._mode_device_id: str | None = loaded_dev
        self._mode_pending_publish: bool = self._mode_value is not None
        self._prms_tables, self._prms_device_id = load_prms_state()
        self._prms_pending_publish: bool = bool(self._prms_tables)
        self._mqtt_was_ready: bool = False
        
        # Režim
        self.mode = ProxyMode.ONLINE
        self.mode_lock = asyncio.Lock()
        
        # Background tasky
        self._replay_task: asyncio.Task[Any] | None = None
        self._replay_failures: dict[int, int] = {}
        self._status_task: asyncio.Task[Any] | None = None
        self._box_conn_lock = asyncio.Lock()
        self._active_box_writer: asyncio.StreamWriter | None = None
        self._active_box_peer: str | None = None
        self._conn_seq: int = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._control_api: ControlAPIServer | None = None
        self._local_setting_pending: dict[str, Any] | None = None

        # Control over MQTT (production)
        self._control_mqtt_enabled: bool = bool(CONTROL_MQTT_ENABLED)
        self._control_set_topic: str = CONTROL_MQTT_SET_TOPIC
        self._control_result_topic: str = CONTROL_MQTT_RESULT_TOPIC
        self._control_qos: int = int(CONTROL_MQTT_QOS)
        self._control_retain: bool = bool(CONTROL_MQTT_RETAIN)
        self._control_box_ready_s: float = float(CONTROL_MQTT_BOX_READY_SECONDS)
        self._control_ack_timeout_s: float = float(CONTROL_MQTT_ACK_TIMEOUT_S)
        self._control_applied_timeout_s: float = float(CONTROL_MQTT_APPLIED_TIMEOUT_S)
        self._control_mode_quiet_s: float = float(CONTROL_MQTT_MODE_QUIET_SECONDS)
        self._control_whitelist: dict[str, set[str]] = CONTROL_WRITE_WHITELIST

        self._control_queue: deque[dict[str, Any]] = deque()
        self._control_inflight: dict[str, Any] | None = None
        self._control_lock = asyncio.Lock()
        self._control_ack_task: asyncio.Task[Any] | None = None
        self._control_applied_task: asyncio.Task[Any] | None = None
        self._control_quiet_task: asyncio.Task[Any] | None = None

        self._box_connected_since_epoch: float | None = None
        self._last_values: dict[tuple[str, str], Any] = {}
        
        # Statistiky
        self.stats = {
            "frames_received": 0,
            "frames_forwarded": 0,
            "frames_queued": 0,
            "acks_local": 0,
            "acks_cloud": 0,
            "mode_changes": 0,
        }
        self.box_connected = False
        self.box_connections = 0
        self._last_data_iso: str | None = None
        self._last_data_epoch: float | None = None
        # IsNewSet/Weather/FW telemetry
        self._isnew_polls = 0
        self._isnew_last_poll_iso: str | None = None
        self._isnew_last_response: str | None = None
        self._isnew_last_rtt_ms: float | None = None
        self._isnew_last_poll_epoch: float | None = None
        # Cloud session telemetry
        self.cloud_connects = 0
        self.cloud_disconnects = 0
        self.cloud_timeouts = 0
        self.cloud_errors = 0
        self.cloud_session_connected = False
    
    async def start(self):
        """Spustí proxy server."""
        self._loop = asyncio.get_running_loop()

        if CONTROL_API_PORT and CONTROL_API_PORT > 0:
            try:
                self._control_api = ControlAPIServer(
                    host=CONTROL_API_HOST,
                    port=CONTROL_API_PORT,
                    proxy=self,
                )
                self._control_api.start()
                logger.info(
                    f"🧪 Control API listening on http://{CONTROL_API_HOST}:{CONTROL_API_PORT}"
                )
            except Exception as e:
                logger.error(f"Control API start failed: {e}")

        # Nastavíme callback pro cloud health změny
        self.cloud_health.set_mode_callback(self._on_cloud_state_change)
        
        # Spustíme background tasky
        await self.cloud_health.start()
        
        # MQTT connect
        if self.mqtt_publisher.connect():
            await self.mqtt_publisher.start_health_check()
        else:
            logger.warning("MQTT: Initial connect failed, health check se pokusí reconnect")
            await self.mqtt_publisher.start_health_check()

        if self._control_mqtt_enabled:
            self._setup_control_mqtt()

        # Pokud máme uložené device_id a aktuální device je AUTO, použij ho
        if self.device_id == "AUTO":
            restored_device_id = self._mode_device_id or self._prms_device_id
            if restored_device_id:
                self.device_id = restored_device_id
                self.mqtt_publisher.device_id = restored_device_id
                logger.info(
                    f"🔑 Obnovuji device_id z uloženého stavu: {self.device_id}"
                )
                self.mqtt_publisher.publish_availability()

        # Po připojení MQTT publikuj stav (init)
        await self.publish_proxy_status()
        await self._publish_mode_if_ready(reason="startup")
        await self._publish_prms_if_ready(reason="startup")
        self._mqtt_was_ready = self.mqtt_publisher.is_ready()
        if self._status_task is None or self._status_task.done():
            self._status_task = asyncio.create_task(self._proxy_status_loop())
        
        # Spustíme TCP server
        server = await asyncio.start_server(
            self.handle_connection,
            PROXY_LISTEN_HOST,
            PROXY_LISTEN_PORT
        )
        
        addr = server.sockets[0].getsockname()
        logger.info(f"🚀 OIG Proxy naslouchá na {addr[0]}:{addr[1]}")
        logger.info(f"📡 Cloud target: {TARGET_SERVER}:{TARGET_PORT}")
        logger.info(f"🔄 Režim: {self.mode.value}")
        
        async with server:
            await server.serve_forever()

    def _build_status_payload(self) -> dict[str, Any]:
        """Vytvoří payload pro proxy_status MQTT sensor."""
        payload = {
            "status": self.mode.value,
            "mode": self.mode.value,
            "box_device_id": self.device_id if self.device_id != "AUTO" else None,
            "cloud_online": int(self.cloud_health.is_online),
            "cloud_connects": self.cloud_connects,
            "cloud_disconnects": self.cloud_disconnects,
            "cloud_timeouts": self.cloud_timeouts,
            "cloud_errors": self.cloud_errors,
            "cloud_session_connected": int(self.cloud_session_connected),
            "cloud_session_active": int(self.cloud_session_connected),
            "cloud_queue": self.cloud_queue.size(),
            "mqtt_queue": self.mqtt_publisher.queue.size(),
            "box_connected": int(self.box_connected),
            "box_connections": self.box_connections,
            "box_connections_active": int(self.box_connected),
            "box_data_recent": int(
                self._last_data_epoch is not None
                and (time.time() - self._last_data_epoch) <= 90
            ),
            "last_data": self._last_data_iso,
            "isnewset_polls": self._isnew_polls,
            "isnewset_last_poll": self._isnew_last_poll_iso,
            "isnewset_last_response": self._isnew_last_response,
            "isnewset_last_rtt_ms": self._isnew_last_rtt_ms,
        }
        return payload

    async def publish_proxy_status(self) -> None:
        """Publikuje stav proxy."""
        payload = self._build_status_payload()
        try:
            await self.mqtt_publisher.publish_proxy_status(payload)
        except Exception as e:
            logger.debug(f"Proxy status publish failed: {e}")

    async def _publish_mode_if_ready(
        self,
        device_id: str | None = None,
        *,
        reason: str | None = None
    ) -> None:
        """Publikuje známý MODE do MQTT (obnova po restartu)."""
        if self._mode_value is None:
            return
        if not self.mqtt_publisher.is_ready():
            self._mode_pending_publish = True
            logger.debug("MODE: MQTT není připraveno, publish odkládám")
            return
        target_device_id = device_id
        if not target_device_id:
            if self.device_id and self.device_id != "AUTO":
                target_device_id = self.device_id
            elif self._mode_device_id:
                target_device_id = self._mode_device_id
        if not target_device_id:
            self._mode_pending_publish = True
            logger.debug("MODE: Nemám device_id, publish odložen")
            return

        payload: dict[str, Any] = {
            "_table": "tbl_box_prms",
            "MODE": int(self._mode_value),
        }
        payload["_device_id"] = target_device_id

        try:
            await self.mqtt_publisher.publish_data(payload)
            self._mode_pending_publish = False
            if reason:
                logger.info(
                    f"MODE: Publikován stav {self._mode_value} ({reason})"
                )
        except Exception as e:
            logger.debug(f"MODE publish failed: {e}")
            self._mode_pending_publish = True

    @staticmethod
    def _should_persist_table(table_name: str | None) -> bool:
        """Vrací True pro tabulky, které chceme perzistovat pro obnovu po restartu."""
        if not table_name or not table_name.startswith("tbl_"):
            return False

        # tbl_actual chodí typicky každých pár sekund → neperzistujeme (zbytečné zápisy)
        if table_name == "tbl_actual":
            return False

        return True

    def _maybe_persist_table_state(
        self,
        parsed: dict[str, Any] | None,
        table_name: str | None,
        device_id: str | None,
    ) -> None:
        """Uloží poslední známé hodnoty pro vybrané tabulky (pro obnovu po restartu)."""
        if not parsed or not table_name:
            return
        if not self._should_persist_table(table_name):
            return

        values = {k: v for k, v in parsed.items() if not k.startswith("_")}
        if not values:
            return

        resolved_device_id = (
            device_id
            or (self.device_id if self.device_id != "AUTO" else None)
            or self._prms_device_id
        )

        save_prms_state(table_name, values, resolved_device_id)

        existing = self._prms_tables.get(table_name, {})
        merged: dict[str, Any] = {}
        if isinstance(existing, dict):
            merged.update(existing)
        merged.update(values)
        self._prms_tables[table_name] = merged
        if resolved_device_id:
            self._prms_device_id = resolved_device_id

    async def _publish_prms_if_ready(self, *, reason: str | None = None) -> None:
        """Publikuje uložené *_prms hodnoty do MQTT (obnova po restartu/reconnectu)."""
        if not self._prms_tables:
            return

        if not self.mqtt_publisher.is_ready():
            self._prms_pending_publish = True
            return

        if self.device_id == "AUTO":
            self._prms_pending_publish = True
            return

        # Publish jen když je potřeba (startup nebo po MQTT reconnectu)
        if not self._prms_pending_publish and reason not in ("startup", "device_autodetect"):
            return

        for table_name, values in self._prms_tables.items():
            if not isinstance(values, dict) or not values:
                continue
            payload: dict[str, Any] = {"_table": table_name, **values}
            try:
                await self.mqtt_publisher.publish_data(payload)
            except Exception as e:
                logger.debug(f"STATE publish failed ({table_name}): {e}")
                self._prms_pending_publish = True
                return

        self._prms_pending_publish = False
        if reason:
            logger.info(f"STATE: Publikován snapshot ({reason})")

    async def _handle_mode_update(
        self,
        new_mode: Any,
        device_id: str | None,
        source: str
    ) -> None:
        """Uloží a publikuje MODE pokud máme nové info."""
        if new_mode is None:
            return
        try:
            mode_int = int(new_mode)
        except Exception:
            return
        if mode_int < 0 or mode_int > 5:
            logger.debug(
                f"MODE: Hodnota {mode_int} mimo rozsah 0-5, zdroj {source}, ignoruji"
            )
            return

        if mode_int != self._mode_value:
            self._mode_value = mode_int
            save_mode_state(mode_int, device_id or self.device_id or self._mode_device_id)
            logger.info(f"MODE: {source} → {mode_int}")
        if device_id:
            self._mode_device_id = device_id

        await self._publish_mode_if_ready(device_id, reason=source)

    async def _maybe_process_mode(
        self,
        parsed: dict[str, Any],
        table_name: str | None,
        device_id: str | None
    ) -> None:
        """Detekuje MODE ze známých zdrojů a zajistí publish + persist."""
        if not parsed:
            return

        if table_name == "tbl_box_prms" and "MODE" in parsed:
            await self._handle_mode_update(parsed.get("MODE"), device_id, "tbl_box_prms")
            return

        if table_name == "tbl_events":
            content = parsed.get("Content")
            if content:
                new_mode = self.parser.parse_mode_from_event(str(content))
                if new_mode is not None:
                    await self._handle_mode_update(new_mode, device_id, "tbl_events")

    def _note_mqtt_ready_transition(self, mqtt_ready: bool) -> None:
        """Při MQTT reconnectu označí perzistované hodnoty k re-publish."""
        if mqtt_ready and not self._mqtt_was_ready:
            if self._mode_value is not None:
                self._mode_pending_publish = True
            if self._prms_tables:
                self._prms_pending_publish = True
        self._mqtt_was_ready = mqtt_ready

    async def _switch_mode(self, new_mode: ProxyMode) -> ProxyMode:
        """Atomicky přepne režim a vrátí předchozí hodnotu."""
        async with self.mode_lock:
            old_mode = self.mode
            if old_mode != new_mode:
                self.mode = new_mode
                self.stats["mode_changes"] += 1
            return old_mode

    def _ensure_replay_task_running(self) -> None:
        if self._replay_task is None or self._replay_task.done():
            self._replay_task = asyncio.create_task(self._replay_cloud_queue())

    async def _maybe_switch_online_to_replay(self, *, reason: str) -> None:
        """Pokud běží ONLINE a cloud fronta není prázdná, přepni do REPLAY."""
        if not self.cloud_health.is_online:
            return
        queue_size = self.cloud_queue.size()
        if queue_size <= 0:
            return

        async with self.mode_lock:
            if self.mode != ProxyMode.ONLINE:
                return
            self.mode = ProxyMode.REPLAY
            self.stats["mode_changes"] += 1

        logger.info(
            "🟡 Režim změněn: ONLINE → REPLAY (%s frames ve frontě, %s)",
            queue_size,
            reason,
        )
        self._ensure_replay_task_running()
        await self.publish_proxy_status()

    async def _proxy_status_loop(self) -> None:
        """Periodicky publikuje proxy_status do MQTT (pro HA restart)."""
        if PROXY_STATUS_INTERVAL <= 0:
            logger.info("Proxy status loop disabled (interval <= 0)")
            return

        logger.info(
            f"Proxy status: periodický publish každých "
            f"{PROXY_STATUS_INTERVAL}s"
        )
        while True:
            await asyncio.sleep(PROXY_STATUS_INTERVAL)
            try:
                mqtt_ready = self.mqtt_publisher.is_ready()
                self._note_mqtt_ready_transition(mqtt_ready)
                await self.publish_proxy_status()
                if self._mode_pending_publish:
                    await self._publish_mode_if_ready(reason="periodic")
                if self._prms_pending_publish:
                    await self._publish_prms_if_ready(reason="periodic")
                await self._maybe_switch_online_to_replay(reason="periodic")
            except Exception as e:
                logger.debug(f"Proxy status loop publish failed: {e}")

    async def _on_cloud_state_change(self, event: str):
        """Callback při změně stavu cloudu."""
        if event == "cloud_down":
            old_mode = await self._switch_mode(ProxyMode.OFFLINE)
            if old_mode != ProxyMode.OFFLINE:
                logger.warning(
                    f"🔴 Režim změněn: {old_mode.value} → {ProxyMode.OFFLINE.value}"
                )
            await self.publish_proxy_status()
            return

        if event == "cloud_recovered":
            queue_size = self.cloud_queue.size()
            new_mode = ProxyMode.REPLAY if queue_size > 0 else ProxyMode.ONLINE
            old_mode = await self._switch_mode(new_mode)
            if old_mode != new_mode:
                if new_mode == ProxyMode.REPLAY:
                    logger.info(
                        f"🟡 Režim změněn: {old_mode.value} → {new_mode.value} "
                        f"({queue_size} frames ve frontě)"
                    )
                    self._ensure_replay_task_running()
                else:
                    logger.info(
                        f"🟢 Režim změněn: {old_mode.value} → {new_mode.value}"
                    )
            await self.publish_proxy_status()

    async def _replay_send_one_frame(
        self,
        frame_id: int,
        table_name: str,
        frame_bytes: bytes,
        *,
        replayed: int,
    ) -> bool:
        reader: asyncio.StreamReader | None = None
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
                timeout=5.0,
            )
            writer.write(frame_bytes)
            await writer.drain()
            await asyncio.wait_for(reader.read(4096), timeout=REPLAY_ACK_TIMEOUT)

            await self.cloud_queue.remove(frame_id)
            self._replay_failures.pop(frame_id, None)

            if replayed % 10 == 0:
                remaining = self.cloud_queue.size()
                logger.info(
                    f"🔄 Replay progress: {replayed} odesláno, {remaining} zbývá"
                )
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "⏱️ Replay ACK timeout (%.1fs) pro frame %s (table=%s)",
                REPLAY_ACK_TIMEOUT,
                frame_id,
                table_name,
            )
            if self._should_drop_replay_frame(table_name, frame_bytes):
                await self._drop_replay_frame(frame_id, table_name, reason="timeout")
                return False
            await self._defer_or_drop_after_retries(frame_id, table_name, reason="timeout")
            return False
        except Exception as e:
            logger.exception(
                "❌ Replay failed pro frame %s (table=%s): %s",
                frame_id,
                table_name,
                repr(e),
            )
            if self._should_drop_replay_frame(table_name, frame_bytes):
                await self._drop_replay_frame(frame_id, table_name, reason="error")
                return False
            await self._defer_or_drop_after_retries(frame_id, table_name, reason="error")
            return False
        finally:
            if writer is not None:
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()

    @staticmethod
    def _looks_like_all_data_sent_end(frame: str) -> bool:
        return "<Result>END</Result>" in frame and "<Reason>All data sent</Reason>" in frame

    async def _drop_replay_frame(self, frame_id: int, table_name: str, *, reason: str) -> None:
        logger.warning(
            "🗑️ Dropping replay frame %s (table=%s, reason=%s)",
            frame_id,
            table_name,
            reason,
        )
        self._replay_failures.pop(frame_id, None)
        await self.cloud_queue.remove(frame_id)

    def _should_drop_replay_frame(self, table_name: str, frame_bytes: bytes) -> bool:
        if table_name == "END":
            frame = frame_bytes.decode("utf-8", errors="replace")
            if self._looks_like_all_data_sent_end(frame):
                return True
        return False

    async def _defer_or_drop_after_retries(
        self,
        frame_id: int,
        table_name: str,
        *,
        reason: str,
    ) -> None:
        failures = self._replay_failures.get(frame_id, 0) + 1
        self._replay_failures[frame_id] = failures
        if failures >= 3:
            await self._drop_replay_frame(
                frame_id,
                table_name,
                reason=f"{reason}_max_retries",
            )
            return
        await self.cloud_queue.defer(frame_id, delay_s=60.0)

    async def _replay_cloud_queue(self) -> None:
        """Background task pro replay cloud fronty (rate limited)."""
        logger.info("🔄 Začínám replay cloud fronty...")
        replayed = 0
        interval = 1.0 / CLOUD_REPLAY_RATE if CLOUD_REPLAY_RATE > 0 else 1.0

        while True:
            if not self.cloud_health.is_online:
                logger.warning("⚠️ Replay přerušeno - cloud offline")
                old_mode = await self._switch_mode(ProxyMode.OFFLINE)
                if old_mode != ProxyMode.OFFLINE:
                    await self.publish_proxy_status()
                break

            item = await self.cloud_queue.get_next()
            if item is None:
                if self.cloud_queue.size() <= 0:
                    logger.info(
                        "✅ Replay dokončen (%s frames), přepínám na ONLINE režim",
                        replayed,
                    )
                    old_mode = await self._switch_mode(ProxyMode.ONLINE)
                    if old_mode != ProxyMode.ONLINE:
                        await self.publish_proxy_status()
                    break

                delay = await self.cloud_queue.next_ready_in()
                await asyncio.sleep(min(delay if delay is not None else 1.0, 5.0))
                continue

            frame_id, _table_name, frame_bytes = item
            success = await self._replay_send_one_frame(
                frame_id, _table_name, frame_bytes, replayed=replayed + 1
            )
            if success:
                replayed += 1

            await asyncio.sleep(interval)

        logger.info(f"🏁 Replay task ukončen (replayed={replayed})")

    async def _register_box_connection(
        self, writer: asyncio.StreamWriter, addr: Any
    ) -> int:
        async with self._box_conn_lock:
            previous = self._active_box_writer
            if previous is not None and not previous.is_closing():
                await self._close_writer(previous)
                logger.info(
                    "BOX: uzavírám předchozí spojení kvůli novému připojení"
                )

            self._conn_seq += 1
            conn_id = self._conn_seq
            self._active_box_writer = writer
            self._active_box_peer = f"{addr[0]}:{addr[1]}" if addr else None
            return conn_id

    @staticmethod
    def _tune_socket(writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        if sock is None:
            return
        with suppress(Exception):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with suppress(Exception):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for opt, val in (
                (getattr(socket, "TCP_KEEPIDLE", None), 60),
                (getattr(socket, "TCP_KEEPINTVL", None), 20),
                (getattr(socket, "TCP_KEEPCNT", None), 3),
            ):
                if opt is None:
                    continue
                with suppress(Exception):
                    sock.setsockopt(socket.IPPROTO_TCP, opt, val)

    async def _unregister_box_connection(self, writer: asyncio.StreamWriter) -> None:
        async with self._box_conn_lock:
            if self._active_box_writer is writer:
                self._active_box_writer = None
                self._active_box_peer = None

    async def handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle jednoho BOX připojení - persistent connection."""
        addr = writer.get_extra_info("peername")
        conn_id = await self._register_box_connection(writer, addr)
        self._tune_socket(writer)

        logger.debug(f"🔌 BOX připojen ({conn_id}): {addr}")
        self.box_connected = True
        self.box_connections += 1
        self._box_connected_since_epoch = time.time()
        await self.publish_proxy_status()

        try:
            await self._handle_box_connection(reader, writer, conn_id)
        except Exception as e:
            logger.error(f"❌ Chyba při zpracování spojení od {addr}: {e}")
        finally:
            await self._close_writer(writer)
            self.box_connected = False
            self._box_connected_since_epoch = None
            await self._unregister_box_connection(writer)
            await self.publish_proxy_status()
    
    async def _handle_online_mode_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        conn_id: int
    ) -> None:
        """Zpětná kompatibilita: ONLINE režim je řešen per-frame v `_handle_box_connection()`."""
        await self._handle_box_connection(box_reader, box_writer, conn_id)

    async def _note_cloud_failure(self, reason: str) -> None:
        """Zaznamená cloud selhání a přepne proxy do OFFLINE, aby se spustil REPLAY."""
        was_online = self.cloud_health.is_online
        self.cloud_health.is_online = False
        self.cloud_health.consecutive_successes = 0
        self.cloud_health.consecutive_failures = self.cloud_health.fail_threshold
        self.cloud_health.last_check_time = time.time()
        if was_online:
            logger.warning(f"☁️ Cloud forced OFFLINE ({reason})")

        async with self.mode_lock:
            current_mode = self.mode
        if current_mode == ProxyMode.ONLINE:
            await self._on_cloud_state_change("cloud_down")

    async def _close_writer(self, writer: asyncio.StreamWriter | None) -> None:
        if writer is None:
            return
        with suppress(Exception):
            writer.close()
            await writer.wait_closed()

    async def _read_box_bytes(
        self,
        reader: asyncio.StreamReader,
        *,
        conn_id: int,
        idle_timeout_s: float,
    ) -> bytes | None:
        try:
            data = await asyncio.wait_for(reader.read(8192), timeout=idle_timeout_s)
        except ConnectionResetError:
            # BOX (nebo síť) spojení tvrdě ukončil – bereme jako běžné odpojení.
            logger.debug(
                "🔌 BOX resetoval spojení (conn=%s)", conn_id
            )
            await self.publish_proxy_status()
            return None
        except asyncio.TimeoutError:
            logger.warning(
                f"⏱️ BOX idle timeout (15 min) - closing session (conn={conn_id})"
            )
            return None

        if not data:
            logger.debug(
                f"🔌 BOX ukončil spojení (EOF, conn={conn_id}, "
                f"frames_rx={self.stats['frames_received']}, "
                f"frames_tx={self.stats['frames_forwarded']}, "
                f"queue={self.cloud_queue.size()})"
            )
            await self.publish_proxy_status()
            return None

        return data

    def _touch_last_data(self) -> None:
        self._last_data_iso = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
        self._last_data_epoch = time.time()

    def _extract_device_and_table(
        self, parsed: dict[str, Any] | None
    ) -> tuple[str | None, str | None]:
        if not parsed:
            return None, None
        device_id = parsed.get("_device_id") if parsed else None
        table_name = parsed.get("_table") if parsed else None
        if (
            not table_name
            and parsed.get("Result") in ("IsNewSet", "IsNewWeather", "IsNewFW")
        ):
            table_name = str(parsed["Result"])
            parsed["_table"] = table_name
        return device_id, table_name

    async def _maybe_autodetect_device_id(self, device_id: str) -> None:
        if not device_id or self.device_id != "AUTO":
            return
        self.device_id = device_id
        self.mqtt_publisher.device_id = device_id
        self.mqtt_publisher.discovery_sent.clear()
        self.mqtt_publisher.publish_availability()
        logger.info(f"🔑 Device ID detected: {device_id}")
        await self._publish_mode_if_ready(device_id=device_id, reason="device_autodetect")
        await self._publish_prms_if_ready(reason="device_autodetect")

    async def _fallback_offline_from_cloud_issue(
        self,
        *,
        reason: str,
        frame_bytes: bytes,
        table_name: str | None,
        device_id: str | None,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter | None,
        note_cloud_failure: bool = True,
    ) -> tuple[None, None]:
        self.cloud_session_connected = False
        await self._close_writer(cloud_writer)
        await self._process_frame_offline(frame_bytes, table_name, device_id, box_writer)
        if note_cloud_failure:
            await self._note_cloud_failure(reason)
        return None, None

    async def _ensure_cloud_connected(
        self,
        cloud_reader: asyncio.StreamReader | None,
        cloud_writer: asyncio.StreamWriter | None,
        *,
        conn_id: int,
        table_name: str | None,
        connect_timeout_s: float,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None]:
        if cloud_writer is not None and not cloud_writer.is_closing():
            return cloud_reader, cloud_writer
        try:
            cloud_reader, cloud_writer = await asyncio.wait_for(
                asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
                timeout=connect_timeout_s,
            )
            self.cloud_connects += 1
            self.cloud_session_connected = True
            logger.debug(f"☁️ Připojeno k {TARGET_SERVER}:{TARGET_PORT}")
            return cloud_reader, cloud_writer
        except Exception as e:
            logger.warning(
                f"⚠️ Cloud nedostupný: {e} - offline mode "
                f"(conn={conn_id}, table={table_name})"
            )
            self.cloud_errors += 1
            self.cloud_session_connected = False
            await self._close_writer(cloud_writer)
            return None, None

    async def _forward_frame_online(
        self,
        *,
        frame_bytes: bytes,
        table_name: str | None,
        device_id: str | None,
        conn_id: int,
        box_writer: asyncio.StreamWriter,
        cloud_reader: asyncio.StreamReader | None,
        cloud_writer: asyncio.StreamWriter | None,
        connect_timeout_s: float,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None]:
        cloud_reader, cloud_writer = await self._ensure_cloud_connected(
            cloud_reader,
            cloud_writer,
            conn_id=conn_id,
            table_name=table_name,
            connect_timeout_s=connect_timeout_s,
        )
        if cloud_writer is None or cloud_reader is None:
            return await self._fallback_offline_from_cloud_issue(
                reason="connect_failed",
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
            )

        try:
            cloud_writer.write(frame_bytes)
            await cloud_writer.drain()
            self.stats["frames_forwarded"] += 1

            ack_data = await asyncio.wait_for(
                cloud_reader.read(4096),
                timeout=CLOUD_ACK_TIMEOUT,
            )
            if not ack_data:
                logger.warning(
                    f"⚠️ Cloud ukončil spojení - offline mode "
                    f"(conn={conn_id}, table={table_name})"
                )
                self.cloud_disconnects += 1
                return await self._fallback_offline_from_cloud_issue(
                    reason="cloud_eof",
                    frame_bytes=frame_bytes,
                    table_name=table_name,
                    device_id=device_id,
                    box_writer=box_writer,
                    cloud_writer=cloud_writer,
                )

            ack_str = ack_data.decode("utf-8", errors="replace")
            capture_payload(
                None,
                table_name,
                ack_str,
                ack_data,
                {},
                direction="cloud_to_proxy",
                length=len(ack_data),
                conn_id=conn_id,
                peer=self._active_box_peer,
            )
            if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW"):
                self._isnew_last_response = self._last_data_iso
                if self._isnew_last_poll_epoch:
                    self._isnew_last_rtt_ms = round(
                        (time.time() - self._isnew_last_poll_epoch) * 1000, 1
                    )

            if table_name:
                self.ack_learner.learn_from_cloud(ack_str, table_name)

            box_writer.write(ack_data)
            await box_writer.drain()
            self.stats["acks_cloud"] += 1
            return cloud_reader, cloud_writer

        except asyncio.TimeoutError:
            logger.warning(
                f"⏱️ Cloud ACK timeout ({CLOUD_ACK_TIMEOUT:.1f}s) - "
                f"offline mode (conn={conn_id}, table={table_name})"
            )
            self.cloud_timeouts += 1
            return await self._fallback_offline_from_cloud_issue(
                reason="ack_timeout",
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
                note_cloud_failure=False,
            )
        except Exception as e:
            logger.warning(
                f"⚠️ Cloud error: {e} - offline mode "
                f"(conn={conn_id}, table={table_name})"
            )
            self.cloud_errors += 1
            return await self._fallback_offline_from_cloud_issue(
                reason="cloud_error",
                frame_bytes=frame_bytes,
                table_name=table_name,
                device_id=device_id,
                box_writer=box_writer,
                cloud_writer=cloud_writer,
            )

    async def _process_box_frame_common(
        self, *, frame_bytes: bytes, frame: str, conn_id: int
    ) -> tuple[str | None, str | None]:
        self.stats["frames_received"] += 1
        self._touch_last_data()

        parsed = self.parser.parse_xml_frame(frame)
        device_id, table_name = self._extract_device_and_table(parsed)
        if table_name is None:
            table_name = self._infer_table_name(frame)
        if device_id is None:
            device_id = self._infer_device_id(frame)
        if device_id:
            await self._maybe_autodetect_device_id(device_id)

        self._maybe_persist_table_state(parsed, table_name, device_id)
        capture_payload(
            device_id,
            table_name,
            frame,
            frame_bytes,
            parsed or {},
            direction="box_to_proxy",
            length=len(frame_bytes),
            conn_id=conn_id,
            peer=self._active_box_peer,
        )

        if parsed:
            if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW"):
                self._isnew_polls += 1
                self._isnew_last_poll_epoch = time.time()
                self._isnew_last_poll_iso = self._last_data_iso
            self._cache_last_values(parsed, table_name)
            await self._control_observe_box_frame(parsed, table_name, frame)
            await self._maybe_process_mode(parsed, table_name, device_id)
            await self.mqtt_publisher.publish_data(parsed)

        return device_id, table_name

    @staticmethod
    def _infer_table_name(frame: str) -> str | None:
        tbl = re.search(r"<TblName>([^<]+)</TblName>", frame)
        if tbl:
            return tbl.group(1)
        res = re.search(r"<Result>([^<]+)</Result>", frame)
        if res:
            return res.group(1)
        return None

    @staticmethod
    def _infer_device_id(frame: str) -> str | None:
        m = re.search(r"<ID_Device>(\\d+)</ID_Device>", frame)
        return m.group(1) if m else None

    async def _get_current_mode(self) -> ProxyMode:
        async with self.mode_lock:
            return self.mode

    async def _handle_frame_offline_mode(
        self,
        *,
        frame_bytes: bytes,
        table_name: str | None,
        device_id: str | None,
        box_writer: asyncio.StreamWriter,
        cloud_writer: asyncio.StreamWriter | None,
    ) -> tuple[None, None]:
        await self._close_writer(cloud_writer)
        self.cloud_session_connected = False
        await self._process_frame_offline(frame_bytes, table_name, device_id, box_writer)
        if self.stats["frames_queued"] % 10 == 0:
            queue_size = self.cloud_queue.size()
            logger.info(
                f"📦 {self.mode.value}: "
                f"{self.stats['frames_queued']} frames queued "
                f"({queue_size} ve frontě)"
            )
        return None, None

    async def _handle_box_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        conn_id: int,
    ) -> None:
        """Jednotný handler pro BOX session, který respektuje změny režimu během spojení."""
        BOX_IDLE_TIMEOUT = 900  # 15 minut
        CLOUD_CONNECT_TIMEOUT = 5.0

        cloud_reader: asyncio.StreamReader | None = None
        cloud_writer: asyncio.StreamWriter | None = None

        try:
            while True:
                data = await self._read_box_bytes(
                    box_reader, conn_id=conn_id, idle_timeout_s=BOX_IDLE_TIMEOUT
                )
                if data is None:
                    break

                frame = data.decode("utf-8", errors="replace")
                try:
                    device_id, table_name = await self._process_box_frame_common(
                        frame_bytes=data, frame=frame, conn_id=conn_id
                    )
                except Exception:
                    # Nechceme shazovat celé BOX spojení kvůli chybě v publish/discovery/parsing.
                    # Traceback nám pomůže najít přesnou příčinu (např. regex v některé knihovně).
                    logger.exception(
                        "❌ Frame processing error (conn=%s, peer=%s)",
                        conn_id,
                        self._active_box_peer,
                    )
                    continue

                if self._maybe_handle_local_setting_ack(frame, box_writer):
                    continue
                current_mode = await self._get_current_mode()

                if current_mode != ProxyMode.ONLINE:
                    cloud_reader, cloud_writer = await self._handle_frame_offline_mode(
                        frame_bytes=data,
                        table_name=table_name,
                        device_id=device_id,
                        box_writer=box_writer,
                        cloud_writer=cloud_writer,
                    )
                    continue

                cloud_reader, cloud_writer = await self._forward_frame_online(
                    frame_bytes=data,
                    table_name=table_name,
                    device_id=device_id,
                    conn_id=conn_id,
                    box_writer=box_writer,
                    cloud_reader=cloud_reader,
                    cloud_writer=cloud_writer,
                    connect_timeout_s=CLOUD_CONNECT_TIMEOUT,
                )

        except ConnectionResetError:
            # Běžné: BOX přeruší TCP (např. reconnect po modem resetu). Nechceme z toho dělat ERROR.
            logger.debug(
                "🔌 BOX ukončil spojení (RST, conn=%s, peer=%s)",
                conn_id,
                self._active_box_peer,
            )
        except Exception:
            logger.exception(
                "❌ Box connection handler error (conn=%s, peer=%s)",
                conn_id,
                self._active_box_peer,
            )
        finally:
            await self._close_writer(cloud_writer)
            self.cloud_session_connected = False
    
    async def _process_frame_offline(
        self,
        frame_bytes: bytes,
        table_name: str | None,
        device_id: str | None,
        box_writer: asyncio.StreamWriter
    ):
        """Zpracuj frame v offline režimu - lokální ACK + queue."""
        ack_response = self.ack_learner.generate_ack(table_name)
        box_writer.write(ack_response.encode('utf-8'))
        await box_writer.drain()
        self.stats["acks_local"] += 1
        
        if table_name and table_name != "tbl_handshake":
            if table_name == "END":
                frame = frame_bytes.decode("utf-8", errors="replace")
                if self._looks_like_all_data_sent_end(frame):
                    logger.debug(
                        "OFFLINE: skipping queue for END/All data sent (no cloud ACK expected)"
                    )
                    return
            await self.cloud_queue.add(frame_bytes, table_name, device_id)
            self.stats["frames_queued"] += 1
    
    async def _handle_offline_mode_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        conn_id: int
    ) -> None:
        """Zpětná kompatibilita: OFFLINE/REPLAY režim je řešen per-frame v `_handle_box_connection()`."""
        await self._handle_box_connection(box_reader, box_writer, conn_id)
    
    def get_stats(self) -> dict[str, Any]:
        """Vrátí statistiky proxy."""
        return {
            "mode": self.mode.value,
            "cloud_online": self.cloud_health.is_online,
            "cloud_queue_size": self.cloud_queue.size(),
            "mqtt_queue_size": self.mqtt_publisher.queue.size(),
            "mqtt_connected": self.mqtt_publisher.connected,
            **self.stats
        }

    # ---------------------------------------------------------------------
    # Control over MQTT (production)
    # ---------------------------------------------------------------------

    def _setup_control_mqtt(self) -> None:
        if self._loop is None:
            return

        def _handler(topic: str, payload: bytes, qos: int, retain: bool) -> None:
            if self._loop is None:
                return
            asyncio.run_coroutine_threadsafe(
                self._control_on_mqtt_message(topic=topic, payload=payload, retain=retain),
                self._loop,
            )

        self.mqtt_publisher.add_message_handler(
            topic=self._control_set_topic,
            handler=_handler,
            qos=self._control_qos,
        )
        logger.info(
            "CONTROL: MQTT enabled (set=%s result=%s)",
            self._control_set_topic,
            self._control_result_topic,
        )

    async def _control_publish_result(
        self,
        *,
        tx: dict[str, Any],
        status: str,
        error: str | None = None,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "tx_id": tx.get("tx_id"),
            "device_id": None if self.device_id == "AUTO" else self.device_id,
            "tbl_name": tx.get("tbl_name"),
            "tbl_item": tx.get("tbl_item"),
            "new_value": tx.get("new_value"),
            "status": status,
            "error": error,
            "detail": detail,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if extra:
            payload.update(extra)
        await self.mqtt_publisher.publish_raw(
            topic=self._control_result_topic,
            payload=json.dumps(payload, ensure_ascii=False),
            qos=self._control_qos,
            retain=self._control_retain,
        )

    @staticmethod
    def _parse_setting_event(content: str) -> tuple[str, str, str | None, str | None] | None:
        m = re.search(
            r"tbl_([a-z0-9_]+)\\s*/\\s*([A-Z0-9_]+):\\s*\\[([^\\]]*)\\]->\\[([^\\]]*)\\]",
            content,
        )
        if not m:
            return None
        tbl_name = f"tbl_{m.group(1)}"
        return tbl_name, m.group(2), m.group(3), m.group(4)

    def _cache_last_values(self, parsed: dict[str, Any], table_name: str | None) -> None:
        if not parsed or not table_name:
            return
        if table_name == "tbl_events":
            return
        for key, value in parsed.items():
            if key.startswith("_"):
                continue
            self._last_values[(table_name, key)] = value

    def _control_is_box_ready(self) -> tuple[bool, str | None]:
        if not self.box_connected:
            return False, "box_not_connected"
        if self.device_id == "AUTO":
            return False, "device_id_unknown"
        if self._box_connected_since_epoch is None:
            return False, "box_not_ready"
        if (time.time() - self._box_connected_since_epoch) < self._control_box_ready_s:
            return False, "box_not_ready"
        if self._last_data_epoch is None:
            return False, "box_not_sending_data"
        if (time.time() - self._last_data_epoch) > 30:
            return False, "box_not_sending_data"
        return True, None

    def _control_normalize_value(
        self, *, tbl_name: str, tbl_item: str, new_value: Any
    ) -> tuple[str, str] | tuple[None, str]:
        raw = new_value
        if isinstance(raw, (int, float)):
            raw_str = str(raw)
        else:
            raw_str = str(raw).strip()

        key = (tbl_name, tbl_item)
        if key == ("tbl_box_prms", "MODE"):
            try:
                mode_int = int(float(raw_str))
            except Exception:
                return None, "bad_value"
            if mode_int < 0 or mode_int > 5:
                return None, "bad_value"
            v = str(mode_int)
            return v, v

        if key in (("tbl_invertor_prm1", "AAC_MAX_CHRG"), ("tbl_invertor_prm1", "A_MAX_CHRG")):
            try:
                f = float(raw_str)
            except Exception:
                return None, "bad_value"
            v = f"{f:.1f}"
            return v, v

        return raw_str, raw_str

    async def _control_on_mqtt_message(
        self, *, topic: str, payload: bytes, retain: bool
    ) -> None:
        try:
            data = json.loads(payload.decode("utf-8", errors="strict"))
        except Exception:
            await self.mqtt_publisher.publish_raw(
                topic=self._control_result_topic,
                payload=json.dumps(
                    {
                        "tx_id": None,
                        "status": "error",
                        "error": "bad_json",
                        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    }
                ),
                qos=self._control_qos,
                retain=self._control_retain,
            )
            return

        tx_id = str(data.get("tx_id") or "").strip()
        tbl_name = str(data.get("tbl_name") or "").strip()
        tbl_item = str(data.get("tbl_item") or "").strip()
        if not tx_id or not tbl_name or not tbl_item or "new_value" not in data:
            await self._control_publish_result(
                tx={"tx_id": tx_id or None, "tbl_name": tbl_name, "tbl_item": tbl_item, "new_value": data.get("new_value")},
                status="error",
                error="missing_fields",
            )
            return

        ok, why = self._control_is_box_ready()
        tx: dict[str, Any] = {
            "tx_id": tx_id,
            "tbl_name": tbl_name,
            "tbl_item": tbl_item,
            "new_value": data.get("new_value"),
            "confirm": str(data.get("confirm") or "New"),
            "received_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if not ok:
            await self._control_publish_result(tx=tx, status="error", error=why)
            return

        allowed = tbl_item in self._control_whitelist.get(tbl_name, set())
        if not allowed:
            await self._control_publish_result(tx=tx, status="error", error="not_allowed")
            return

        send_value, err = self._control_normalize_value(
            tbl_name=tbl_name, tbl_item=tbl_item, new_value=tx["new_value"]
        )
        if send_value is None:
            await self._control_publish_result(tx=tx, status="error", error=err)
            return
        tx["new_value"] = send_value
        tx["_canon"] = send_value

        current = self._last_values.get((tbl_name, tbl_item))
        if current is not None:
            current_norm, _ = self._control_normalize_value(
                tbl_name=tbl_name, tbl_item=tbl_item, new_value=current
            )
            if current_norm is not None and str(current_norm) == str(send_value):
                await self._control_publish_result(
                    tx=tx, status="completed", detail="noop_already_set"
                )
                return

        async with self._control_lock:
            if self._control_inflight is not None:
                infl = self._control_inflight
                if (
                    infl.get("tbl_name") == tbl_name
                    and infl.get("tbl_item") == tbl_item
                    and infl.get("_canon") == tx.get("_canon")
                ):
                    await self._control_publish_result(
                        tx=tx, status="completed", detail="duplicate_ignored"
                    )
                    return
            for q in self._control_queue:
                if (
                    q.get("tbl_name") == tbl_name
                    and q.get("tbl_item") == tbl_item
                    and q.get("_canon") == tx.get("_canon")
                ):
                    await self._control_publish_result(
                        tx=tx, status="completed", detail="duplicate_ignored"
                    )
                    return

            self._control_queue.append(tx)

        await self._control_publish_result(tx=tx, status="accepted")
        await self._control_maybe_start_next()

    async def _control_maybe_start_next(self) -> None:
        async with self._control_lock:
            if self._control_inflight is not None:
                return
            if not self._control_queue:
                return
            tx = self._control_queue.popleft()
            self._control_inflight = tx
            tx["stage"] = "accepted"

        await self._control_start_inflight()

    async def _control_start_inflight(self) -> None:
        async with self._control_lock:
            tx = self._control_inflight
        if tx is None:
            return

        result = await self._send_setting_to_box(
            tbl_name=str(tx["tbl_name"]),
            tbl_item=str(tx["tbl_item"]),
            new_value=str(tx["new_value"]),
            confirm=str(tx.get("confirm") or "New"),
            tx_id=str(tx["tx_id"]),
        )
        if not result.get("ok"):
            await self._control_publish_result(
                tx=tx, status="error", error=str(result.get("error") or "send_failed")
            )
            await self._control_finish_inflight()
            return

        tx["stage"] = "sent_to_box"
        tx["id"] = result.get("id")
        tx["id_set"] = result.get("id_set")
        tx["sent_at_mono"] = time.monotonic()

        await self._control_publish_result(
            tx=tx,
            status="sent_to_box",
            extra={"id": tx.get("id"), "id_set": tx.get("id_set")},
        )

        if self._control_ack_task and not self._control_ack_task.done():
            self._control_ack_task.cancel()
        self._control_ack_task = asyncio.create_task(self._control_ack_timeout())

    async def _control_on_box_setting_ack(self, *, tx_id: str | None, ack: bool) -> None:
        if not tx_id:
            return
        async with self._control_lock:
            tx = self._control_inflight
            if tx is None or str(tx.get("tx_id")) != str(tx_id):
                return
            tx["stage"] = "box_ack" if ack else "error"

        if self._control_ack_task and not self._control_ack_task.done():
            self._control_ack_task.cancel()
        self._control_ack_task = None

        if not ack:
            await self._control_publish_result(tx=tx, status="error", error="box_nack")
            await self._control_finish_inflight()
            return

        await self._control_publish_result(tx=tx, status="box_ack")

        if self._control_applied_task and not self._control_applied_task.done():
            self._control_applied_task.cancel()
        self._control_applied_task = asyncio.create_task(self._control_applied_timeout())

    async def _control_ack_timeout(self) -> None:
        await asyncio.sleep(self._control_ack_timeout_s)
        async with self._control_lock:
            tx = self._control_inflight
            if tx is None:
                return
            if tx.get("stage") not in ("sent_to_box", "accepted"):
                return
        await self._control_publish_result(
            tx=tx, status="error", error="timeout_waiting_ack"
        )
        await self._control_finish_inflight()

    async def _control_applied_timeout(self) -> None:
        await asyncio.sleep(self._control_applied_timeout_s)
        async with self._control_lock:
            tx = self._control_inflight
            if tx is None:
                return
            if tx.get("stage") in ("applied", "completed", "error"):
                return
        await self._control_publish_result(
            tx=tx, status="error", error="timeout_waiting_applied"
        )
        await self._control_finish_inflight()

    async def _control_quiet_wait(self) -> None:
        while True:
            async with self._control_lock:
                tx = self._control_inflight
                if tx is None:
                    return
                if tx.get("stage") not in ("applied",):
                    return
                last = float(tx.get("last_inv_ack_mono") or tx.get("applied_at_mono") or 0.0)
                wait_s = max(0.0, (last + self._control_mode_quiet_s) - time.monotonic())
            if wait_s > 0:
                await asyncio.sleep(wait_s)
                continue
            break

        async with self._control_lock:
            tx = self._control_inflight
            if tx is None:
                return
            if tx.get("stage") != "applied":
                return
        await self._control_publish_result(tx=tx, status="completed", detail="quiet_window")
        await self._control_finish_inflight()

    async def _control_finish_inflight(self) -> None:
        async with self._control_lock:
            self._control_inflight = None
            for task in (self._control_ack_task, self._control_applied_task, self._control_quiet_task):
                if task and not task.done():
                    task.cancel()
            self._control_ack_task = None
            self._control_applied_task = None
            self._control_quiet_task = None
        await self._control_maybe_start_next()

    async def _control_observe_box_frame(
        self, parsed: dict[str, Any], table_name: str | None, frame: str
    ) -> None:
        async with self._control_lock:
            tx = self._control_inflight
        if tx is None or not parsed or not table_name:
            return

        if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW", "END"):
            async with self._control_lock:
                tx2 = self._control_inflight
                if tx2 is None or tx2.get("tx_id") != tx.get("tx_id"):
                    return
                stage = tx2.get("stage")
            if stage in ("box_ack", "applied"):
                await self._control_publish_result(
                    tx=tx, status="completed", detail=f"box_marker:{table_name}"
                )
                await self._control_finish_inflight()
            return

        if table_name != "tbl_events":
            return

        content = parsed.get("Content")
        typ = parsed.get("Type")
        if not content or not isinstance(content, str):
            return

        if typ == "Setting":
            ev = self._parse_setting_event(content)
            if not ev:
                return
            ev_tbl, ev_item, old_v, new_v = ev
            if ev_tbl != tx.get("tbl_name") or ev_item != tx.get("tbl_item"):
                return
            desired = str(tx.get("new_value"))
            if str(new_v) != desired:
                return
            async with self._control_lock:
                tx2 = self._control_inflight
                if tx2 is None or tx2.get("tx_id") != tx.get("tx_id"):
                    return
                tx2["stage"] = "applied"
                tx2["applied_at_mono"] = time.monotonic()
                tx2["last_inv_ack_mono"] = tx2["applied_at_mono"]
            await self._control_publish_result(
                tx=tx, status="applied", extra={"old_value": old_v, "observed_new_value": new_v}
            )

            if (tx.get("tbl_name"), tx.get("tbl_item")) != ("tbl_box_prms", "MODE"):
                await self._control_publish_result(tx=tx, status="completed", detail="applied")
                await self._control_finish_inflight()
                return

            if self._control_quiet_task and not self._control_quiet_task.done():
                self._control_quiet_task.cancel()
            self._control_quiet_task = asyncio.create_task(self._control_quiet_wait())
            return

        if "Invertor ACK" in content and (tx.get("tbl_name"), tx.get("tbl_item")) == ("tbl_box_prms", "MODE"):
            async with self._control_lock:
                tx2 = self._control_inflight
                if tx2 is None or tx2.get("tx_id") != tx.get("tx_id"):
                    return
                if tx2.get("stage") != "applied":
                    return
                tx2["last_inv_ack_mono"] = time.monotonic()
            if self._control_quiet_task and not self._control_quiet_task.done():
                self._control_quiet_task.cancel()
            self._control_quiet_task = asyncio.create_task(self._control_quiet_wait())

    # ---------------------------------------------------------------------
    # Control API (prototype)
    # ---------------------------------------------------------------------

    def get_control_api_health(self) -> dict[str, Any]:
        now = time.time()
        last_age_s: float | None = None
        if self._last_data_epoch is not None:
            last_age_s = max(0.0, now - self._last_data_epoch)
        return {
            "ok": True,
            "device_id": None if self.device_id == "AUTO" else self.device_id,
            "box_connected": bool(self.box_connected),
            "box_peer": self._active_box_peer,
            "box_data_age_s": last_age_s,
        }

    def control_api_send_setting(
        self,
        *,
        tbl_name: str,
        tbl_item: str,
        new_value: str,
        confirm: str = "New",
    ) -> dict[str, Any]:
        if self._loop is None:
            return {"ok": False, "error": "event_loop_not_ready"}

        fut = asyncio.run_coroutine_threadsafe(
            self._send_setting_to_box(
                tbl_name=tbl_name,
                tbl_item=tbl_item,
                new_value=new_value,
                confirm=confirm,
            ),
            self._loop,
        )
        try:
            return fut.result(timeout=5.0)
        except Exception as e:
            return {"ok": False, "error": f"send_failed:{type(e).__name__}"}

    async def _send_setting_to_box(
        self,
        *,
        tbl_name: str,
        tbl_item: str,
        new_value: str,
        confirm: str,
        tx_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.box_connected:
            return {"ok": False, "error": "box_not_connected"}
        if self._last_data_epoch is None or (time.time() - self._last_data_epoch) > 30:
            return {"ok": False, "error": "box_not_sending_data"}
        if self.device_id == "AUTO":
            return {"ok": False, "error": "device_id_unknown"}

        async with self._box_conn_lock:
            writer = self._active_box_writer
        if writer is None:
            return {"ok": False, "error": "no_active_box_writer"}

        msg_id = random.randint(10_000_000, 99_999_999)
        id_set = int(time.time())
        now_local = datetime.now()
        now_utc = datetime.now(timezone.utc)

        inner = (
            f"<ID>{msg_id}</ID>"
            f"<ID_Device>{self.device_id}</ID_Device>"
            f"<ID_Set>{id_set}</ID_Set>"
            "<ID_SubD>0</ID_SubD>"
            f"<DT>{now_local.strftime('%d.%m.%Y %H:%M:%S')}</DT>"
            f"<NewValue>{new_value}</NewValue>"
            f"<Confirm>{confirm}</Confirm>"
            f"<TblName>{tbl_name}</TblName>"
            f"<TblItem>{tbl_item}</TblItem>"
            "<ID_Server>5</ID_Server>"
            "<mytimediff>0</mytimediff>"
            "<Reason>Setting</Reason>"
            f"<TSec>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</TSec>"
            "<ver>55734</ver>"
        )
        frame = build_frame(inner, add_crlf=True).encode("utf-8", errors="strict")

        self._local_setting_pending = {
            "sent_at": time.monotonic(),
            "tbl_name": tbl_name,
            "tbl_item": tbl_item,
            "new_value": new_value,
            "id": msg_id,
            "id_set": id_set,
            "tx_id": tx_id,
        }

        writer.write(frame)
        await writer.drain()

        logger.info(
            "CONTROL: Sent Setting %s/%s=%s (id=%s id_set=%s)",
            tbl_name,
            tbl_item,
            new_value,
            msg_id,
            id_set,
        )

        return {
            "ok": True,
            "sent": True,
            "device_id": self.device_id,
            "id": msg_id,
            "id_set": id_set,
        }

    def _maybe_handle_local_setting_ack(
        self, frame: str, box_writer: asyncio.StreamWriter
    ) -> bool:
        pending = self._local_setting_pending
        if not pending:
            return False
        if (time.monotonic() - float(pending.get("sent_at", 0.0))) > self._control_ack_timeout_s:
            return False
        if "<Reason>Setting</Reason>" not in frame:
            return False
        if "<Result>ACK</Result>" not in frame and "<Result>NACK</Result>" not in frame:
            return False

        ack_ok = "<Result>ACK</Result>" in frame
        tx_id = pending.get("tx_id")

        now_local = datetime.now()
        now_utc = datetime.now(timezone.utc)
        end_inner = (
            "<Result>END</Result>"
            f"<Time>{now_local.strftime('%Y-%m-%d %H:%M:%S')}</Time>"
            f"<UTCTime>{now_utc.strftime('%Y-%m-%d %H:%M:%S')}</UTCTime>"
        )
        end_frame = build_frame(end_inner, add_crlf=True).encode("utf-8", errors="strict")

        box_writer.write(end_frame)
        try:
            asyncio.create_task(box_writer.drain())
        except Exception:
            pass

        try:
            asyncio.create_task(self._control_on_box_setting_ack(tx_id=str(tx_id) if tx_id else None, ack=ack_ok))
        except Exception:
            pass

        logger.info(
            "CONTROL: BOX responded to local Setting (sent END), last=%s/%s=%s",
            pending.get("tbl_name"),
            pending.get("tbl_item"),
            pending.get("new_value"),
        )
        self._local_setting_pending = None
        return True
