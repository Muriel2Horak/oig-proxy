#!/usr/bin/env python3
"""
OIG Proxy - hlavní orchestrace s ONLINE/OFFLINE/REPLAY režimy.
"""

import asyncio
import logging
import time
from typing import Any

from cloud_manager import ACKLearner, CloudHealthChecker, CloudQueue
from config import (
    CLOUD_REPLAY_RATE,
    PROXY_LISTEN_HOST,
    PROXY_LISTEN_PORT,
    PROXY_STATUS_INTERVAL,
    TARGET_PORT,
    TARGET_SERVER,
)
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
        self._status_task: asyncio.Task[Any] | None = None
        self._box_conn_lock = asyncio.Lock()
        self._active_box_writer: asyncio.StreamWriter | None = None
        self._active_box_peer: str | None = None
        self._conn_seq: int = 0
        
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
        await self.publish_proxy_status(force=True)
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

    async def publish_proxy_status(self, *, force: bool = False) -> None:
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
        if mode_int < 0 or mode_int > 3:
            logger.debug(
                f"MODE: Hodnota {mode_int} mimo rozsah 0-3, zdroj {source}, ignoruji"
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
                    return

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
                if mqtt_ready and not self._mqtt_was_ready:
                    # MQTT reconnect → obnov i perzistované hodnoty, které chodí zřídka
                    if self._mode_value is not None:
                        self._mode_pending_publish = True
                    if self._prms_tables:
                        self._prms_pending_publish = True
                self._mqtt_was_ready = mqtt_ready
                await self.publish_proxy_status()
                await self._publish_mode_if_ready(reason="periodic")
                await self._publish_prms_if_ready(reason="periodic")
            except Exception as e:
                logger.debug(f"Proxy status loop publish failed: {e}")
    
    async def _on_cloud_state_change(self, event: str):
        """Callback při změně stavu cloudu."""
        async with self.mode_lock:
            old_mode = self.mode
            
            if event == "cloud_down":
                # Cloud vypadl → OFFLINE režim
                self.mode = ProxyMode.OFFLINE
                logger.warning(
                    f"🔴 Režim změněn: {old_mode.value} → {self.mode.value}"
                )
                self.stats["mode_changes"] += 1
                await self.publish_proxy_status(force=True)
                
            elif event == "cloud_recovered":
                # Cloud se vrátil
                queue_size = self.cloud_queue.size()
                
                if queue_size > 0:
                    # Máme frontu → REPLAY režim
                    self.mode = ProxyMode.REPLAY
                    logger.info(
                        f"🟡 Režim změněn: {old_mode.value} → {self.mode.value} "
                        f"({queue_size} frames ve frontě)"
                    )
                    self.stats["mode_changes"] += 1
                    
                    # Spustíme replay task
                    if self._replay_task is None or self._replay_task.done():
                        self._replay_task = asyncio.create_task(
                            self._replay_cloud_queue()
                        )
                else:
                    # Fronta prázdná → rovnou ONLINE
                    self.mode = ProxyMode.ONLINE
                    logger.info(
                        f"🟢 Režim změněn: {old_mode.value} → {self.mode.value}"
                    )
                    self.stats["mode_changes"] += 1
                await self.publish_proxy_status(force=True)
    
    async def _replay_cloud_queue(self):
        """Background task pro replay cloud fronty (rate limited)."""
        logger.info("🔄 Začínám replay cloud fronty...")
        replayed = 0
        interval = 1.0 / CLOUD_REPLAY_RATE  # ~1s pro 1 frame/s
        
        while True:
            # Check zda cloud je stále online
            if not self.cloud_health.is_online:
                logger.warning("⚠️ Replay přerušeno - cloud offline")
                async with self.mode_lock:
                    self.mode = ProxyMode.OFFLINE
                break
            
            # Vezmi další frame z fronty
            item = await self.cloud_queue.get_next()
            if not item:
                # Fronta prázdná → přepni na ONLINE
                logger.info(
                    f"✅ Replay dokončen ({replayed} frames), "
                    "přepínám na ONLINE režim"
                )
                async with self.mode_lock:
                    self.mode = ProxyMode.ONLINE
                    self.stats["mode_changes"] += 1
                break
            
            frame_id, table_name, frame_bytes = item
            
            # Pošli na cloud
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
                    timeout=5.0
                )
                
                # Pošli frame
                writer.write(frame_bytes)
                await writer.drain()
                
                # Čekej na ACK (timeout 3s)
                await asyncio.wait_for(
                    reader.read(4096),
                    timeout=3.0
                )
                
                writer.close()
                await writer.wait_closed()
                
                # Úspěch → odstraň z fronty
                await self.cloud_queue.remove(frame_id)
                replayed += 1
                
                # Log progress
                if replayed % 10 == 0:
                    remaining = self.cloud_queue.size()
                    logger.info(
                        f"🔄 Replay progress: {replayed} odesláno, "
                        f"{remaining} zbývá"
                    )
                
            except Exception as e:
                logger.error(f"❌ Replay failed pro frame {frame_id}: {e}")
                # Necháme frame ve frontě, zkusíme další
            
            # Rate limiting
            await asyncio.sleep(interval)
        
        logger.info(f"🏁 Replay task ukončen (replayed={replayed})")
    
    async def handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        """Handle jednoho BOX připojení - persistent connection."""
        addr = writer.get_extra_info('peername')
        async with self._box_conn_lock:
            if self._active_box_writer and not self._active_box_writer.is_closing():
                try:
                    self._active_box_writer.close()
                    await self._active_box_writer.wait_closed()
                    logger.info("BOX: uzavírám předchozí spojení kvůli novému připojení")
                except Exception:
                    pass
            self._conn_seq += 1
            conn_id = self._conn_seq
            self._active_box_writer = writer
            self._active_box_peer = f"{addr[0]}:{addr[1]}" if addr else None

        logger.debug(f"🔌 BOX připojen ({conn_id}): {addr}")
        self.box_connected = True
        self.box_connections += 1
        await self.publish_proxy_status(force=True)
        
        try:
            # Zpracuj podle aktuálního režimu
            async with self.mode_lock:
                current_mode = self.mode
            
            if current_mode == ProxyMode.ONLINE:
                await self._handle_online_mode_connection(reader, writer, conn_id)
            else:
                # OFFLINE nebo REPLAY → lokální ACK + queue
                await self._handle_offline_mode_connection(reader, writer, conn_id)
            
        except Exception as e:
            logger.error(f"❌ Chyba při zpracování spojení od {addr}: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            self.box_connected = False
            async with self._box_conn_lock:
                if self._active_box_writer is writer:
                    self._active_box_writer = None
                    self._active_box_peer = None
            await self.publish_proxy_status(force=True)
    
    async def _handle_online_mode_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        conn_id: int
    ):
        """
        ONLINE režim s reconnect logikou:
        - Drží BOX spojení aktivní
        - Pro každý frame se pokusí forward na cloud
        - Pokud cloud selže/ukončí, vytvoří nové cloud spojení
        - Pokud cloud nedostupný, offline mode pro daný frame
        - Timeout 15 min na BOX idle (detekce mrtvého BOXu)
        """
        BOX_IDLE_TIMEOUT = 900  # 15 minut
        CLOUD_CONNECT_TIMEOUT = 5.0
        CLOUD_ACK_TIMEOUT = 10.0
        
        cloud_reader = None
        cloud_writer = None
        
        try:
            while True:
                # Čti frame od BOX s timeoutem (detekce mrtvého BOXu)
                try:
                    data = await asyncio.wait_for(
                        box_reader.read(8192),
                        timeout=BOX_IDLE_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"⏱️ BOX idle timeout (15 min) - closing session (conn={conn_id})"
                    )
                    break
                
                if not data:
                    logger.debug(
                        f"🔌 BOX ukončil spojení (EOF, conn={conn_id}, "
                        f"frames_rx={self.stats['frames_received']}, "
                        f"frames_tx={self.stats['frames_forwarded']}, "
                        f"queue={self.cloud_queue.size()})"
                    )
                    await self.publish_proxy_status(force=True)
                    break
                
                # Zpracuj frame
                frame = data.decode('utf-8', errors='replace')
                self.stats["frames_received"] += 1
                self._last_data_iso = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
                self._last_data_epoch = time.time()
                
                # Parse & capture
                parsed = self.parser.parse_xml_frame(frame)
                device_id = parsed.get("_device_id") if parsed else None
                table_name = parsed.get("_table") if parsed else None
                # IsNew* frames nemají TblName, vezmeme z Result
                if (not table_name and parsed and parsed.get("Result") in ("IsNewSet", "IsNewWeather", "IsNewFW")):
                    table_name = parsed["Result"]
                    parsed["_table"] = table_name
                
                # Auto-detect device_id from BOX frames
                if device_id and self.device_id == "AUTO":
                    self.device_id = device_id
                    self.mqtt_publisher.device_id = device_id
                    # Clear discovery cache to re-send with correct device_id
                    self.mqtt_publisher.discovery_sent.clear()
                    # Re-publish availability with correct device_id
                    self.mqtt_publisher.publish_availability()
                    logger.info(f"🔑 Device ID detected: {device_id}")
                    await self._publish_mode_if_ready(device_id=device_id, reason="device_autodetect")
                    await self._publish_prms_if_ready(reason="device_autodetect")

                # Persist *_prms tabulky (chodí zřídka) pro obnovu po restartu
                self._maybe_persist_table_state(parsed, table_name, device_id)
                
                capture_payload(
                    device_id, table_name, frame, parsed or {},
                    direction="box_to_proxy", length=len(frame),
                    conn_id=conn_id, peer=self._active_box_peer
                )
                
                # MQTT publish (vždy, nezávisle na cloud)
                if parsed:
                    # IsNew* telemetry
                    if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW"):
                        self._isnew_polls += 1
                        self._isnew_last_poll_epoch = time.time()
                        self._isnew_last_poll_iso = self._last_data_iso
                    await self._maybe_process_mode(parsed, table_name, device_id)
                    await self.mqtt_publisher.publish_data(parsed)
                
                # Pokud nemáme cloud spojení, vytvoř nové
                if cloud_writer is None or cloud_writer.is_closing():
                    try:
                        cloud_reader, cloud_writer = await asyncio.wait_for(
                            asyncio.open_connection(
                                TARGET_SERVER, TARGET_PORT
                            ),
                            timeout=CLOUD_CONNECT_TIMEOUT
                        )
                        self.cloud_connects += 1
                        self.cloud_session_connected = True
                        logger.debug(
                            f"☁️ Připojeno k {TARGET_SERVER}:{TARGET_PORT}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"⚠️ Cloud nedostupný: {e} - offline mode "
                            f"(conn={conn_id}, table={table_name})"
                        )
                        # Cloud nedostupný → offline mode pro tento frame
                        await self._process_frame_offline(
                            data, table_name, device_id, box_writer
                        )
                        await self.publish_proxy_status(force=True)
                        continue
                
                # Forward na cloud
                try:
                    cloud_writer.write(data)
                    await cloud_writer.drain()
                    self.stats["frames_forwarded"] += 1
                    
                    # Čekej na ACK od cloudu
                    ack_data = await asyncio.wait_for(
                        cloud_reader.read(4096),
                        timeout=CLOUD_ACK_TIMEOUT
                    )
                    
                    if not ack_data:
                        # Cloud ukončil spojení (EOF)
                        logger.warning(
                            f"⚠️ Cloud ukončil spojení - reconnect next frame "
                            f"(conn={conn_id}, table={table_name})"
                        )
                        self.cloud_disconnects += 1
                        self.cloud_session_connected = False
                        cloud_writer.close()
                        cloud_writer = None
                        # Tento frame musíme zpracovat offline
                        await self._process_frame_offline(
                            frame, table_name, device_id, box_writer
                        )
                        continue
                    
                    # Capture cloud response
                    ack_str = ack_data.decode('utf-8')
                    capture_payload(
                        None, table_name, ack_str, {},
                        direction="cloud_to_proxy", length=len(ack_data),
                        conn_id=conn_id, peer=self._active_box_peer
                    )
                    if table_name in ("IsNewSet", "IsNewWeather", "IsNewFW"):
                        self._isnew_last_response = self._last_data_iso
                        if self._isnew_last_poll_epoch:
                            self._isnew_last_rtt_ms = round(
                                (time.time() - self._isnew_last_poll_epoch) * 1000, 1
                            )
                    
                    # ACK Learning
                    if table_name:
                        self.ack_learner.learn_from_cloud(ack_str, table_name)
                    
                    # Forward ACK na BOX
                    box_writer.write(ack_data)
                    await box_writer.drain()
                    self.stats["acks_cloud"] += 1
                    
                except asyncio.TimeoutError:
                    logger.warning(
                        f"⏱️ Cloud ACK timeout - offline mode for this frame "
                        f"(conn={conn_id}, table={table_name})"
                    )
                    self.cloud_timeouts += 1
                    self.cloud_session_connected = False
                    if cloud_writer:
                        cloud_writer.close()
                    cloud_writer = None
                    await self._process_frame_offline(
                        data, table_name, device_id, box_writer
                    )
                    
                except Exception as e:
                    logger.warning(
                        f"⚠️ Cloud error: {e} - offline mode for this frame "
                        f"(conn={conn_id}, table={table_name})"
                    )
                    self.cloud_errors += 1
                    self.cloud_session_connected = False
                    if cloud_writer:
                        try:
                            cloud_writer.close()
                        except Exception:
                            pass
                    cloud_writer = None
                    await self._process_frame_offline(
                        data, table_name, device_id, box_writer
                    )
                    
        except Exception as e:
            logger.error(f"❌ Online mode error: {e}")
        finally:
            if cloud_writer:
                try:
                    cloud_writer.close()
                    await cloud_writer.wait_closed()
                except Exception:
                    pass
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
            await self.cloud_queue.add(frame_bytes, table_name, device_id)
            self.stats["frames_queued"] += 1
    
    async def _handle_offline_mode_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        conn_id: int
    ):
        """
        OFFLINE/REPLAY režim - persistent connection s lokálním ACK.
        Timeout 15 min na BOX idle (detekce mrtvého BOXu).
        """
        BOX_IDLE_TIMEOUT = 900  # 15 minut
        
        try:
            while True:
                # Čti frame od BOX s timeoutem
                try:
                    data = await asyncio.wait_for(
                        box_reader.read(8192),
                        timeout=BOX_IDLE_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"⏱️ BOX idle timeout (15 min) - closing session (conn={conn_id})"
                    )
                    break
                
                if not data:
                    logger.debug(
                        f"🔌 BOX ukončil spojení (EOF, conn={conn_id}, "
                        f"frames_rx={self.stats['frames_received']}, "
                        f"queue={self.cloud_queue.size()})"
                    )
                    break
                
                frame = data.decode('utf-8', errors='replace')
                self.stats["frames_received"] += 1
                self._last_data_iso = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
                self._last_data_epoch = time.time()
                
                # Parse & capture
                parsed = self.parser.parse_xml_frame(frame)
                device_id = parsed.get("_device_id") if parsed else None
                table_name = parsed.get("_table") if parsed else None
                # IsNew* frames nemají TblName, vezmeme z Result
                if (not table_name and parsed and parsed.get("Result") in ("IsNewSet", "IsNewWeather", "IsNewFW")):
                    table_name = parsed["Result"]
                    parsed["_table"] = table_name

                # Auto-detect device_id from BOX frames
                if device_id and self.device_id == "AUTO":
                    self.device_id = device_id
                    self.mqtt_publisher.device_id = device_id
                    # Clear discovery cache to re-send with correct device_id
                    self.mqtt_publisher.discovery_sent.clear()
                    # Re-publish availability with correct device_id
                    self.mqtt_publisher.publish_availability()
                    logger.info(f"🔑 Device ID detected: {device_id}")
                    await self._publish_mode_if_ready(device_id=device_id, reason="device_autodetect")
                    await self._publish_prms_if_ready(reason="device_autodetect")

                # Persist *_prms tabulky (chodí zřídka) pro obnovu po restartu
                self._maybe_persist_table_state(parsed, table_name, device_id)
                
                capture_payload(
                    device_id, table_name, frame, parsed or {},
                    direction="box_to_proxy", length=len(frame),
                    conn_id=conn_id, peer=self._active_box_peer
                )
                
                # MQTT publish
                if parsed:
                    await self._maybe_process_mode(parsed, table_name, device_id)
                    await self.mqtt_publisher.publish_data(parsed)
                
                # Lokální ACK + queue
                await self._process_frame_offline(
                    data, table_name, device_id, box_writer
                )
                
                # Log každých 10 frames
                if self.stats["frames_queued"] % 10 == 0:
                    queue_size = self.cloud_queue.size()
                    logger.info(
                        f"📦 {self.mode.value}: "
                        f"{self.stats['frames_queued']} frames queued "
                        f"({queue_size} ve frontě)"
                    )
                    
        except Exception as e:
            logger.debug(f"Offline mode ukončen: {e}")
    
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
