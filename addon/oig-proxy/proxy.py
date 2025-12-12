#!/usr/bin/env python3
"""
OIG Proxy - hlavní orchestrace s ONLINE/OFFLINE/REPLAY režimy.
"""

import asyncio
import logging
import time
from typing import Any

from cloud_manager import ACKLearner, CloudHealthChecker, CloudQueue
from cloud_session import CloudSessionManager
from config import (
    CLOUD_REPLAY_RATE,
    PROXY_LISTEN_HOST,
    PROXY_LISTEN_PORT,
    TARGET_PORT,
    TARGET_SERVER,
)
from models import ProxyMode
from mqtt_publisher import MQTTPublisher
from parser import OIGDataParser
from utils import capture_payload, iso_now

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
        self.cloud_session = CloudSessionManager(TARGET_SERVER, TARGET_PORT)
        self.ack_learner = ACKLearner()
        self.mqtt_publisher = MQTTPublisher(device_id)
        self.parser = OIGDataParser()
        
        # Režim
        self.mode = ProxyMode.ONLINE
        self.mode_lock = asyncio.Lock()
        self.last_data_iso: str | None = None
        self._last_data_epoch: float | None = None
        self._ever_seen_box = False
        self.box_tcp_connected = False
        self._last_status_publish = 0.0
        self._box_conn_count = 0
        self._box_conn_lock = asyncio.Lock()

        # IsNewSet telemetry (BOX → Cloud poll)
        self.isnewset_polls = 0
        self.isnewset_last_poll_iso: str | None = None
        self.isnewset_last_response: str | None = None
        self.isnewset_last_response_iso: str | None = None
        self.isnewset_last_rtt_ms: int | None = None
        self._pending_isnewset_start: float | None = None
        
        # Background tasks
        self._replay_task: asyncio.Task[Any] | None = None
        self._status_task: asyncio.Task[Any] | None = None
        
        # Statistiky
        self.stats = {
            "frames_received": 0,
            "frames_forwarded": 0,
            "frames_queued": 0,
            "acks_local": 0,
            "acks_cloud": 0,
            "mode_changes": 0,
        }

    def _compute_status(self) -> str:
        """Odvodí čitelný stav proxy pro MQTT status senzor."""
        now = time.time()
        if not self._ever_seen_box:
            return "Čeká na BOX"
        if self._last_data_epoch is None or (now - self._last_data_epoch) > 90:
            return "Čeká na data"
        if self.mode == ProxyMode.REPLAY:
            return "Vyprazňování fronty"
        if self.mode == ProxyMode.OFFLINE:
            return "Offline"
        return "Online"

    async def publish_proxy_status(self, force: bool = False) -> None:
        """Publikuje stav proxy (stav + telemetrie front) na MQTT."""
        now = time.time()
        if not force and (now - self._last_status_publish) < 30:
            return
        
        status = self._compute_status()
        now = time.time()
        box_data_recent = int(
            self._last_data_epoch is not None and (now - self._last_data_epoch) <= 90
        )
        payload = {
            "status": status,
            # mode ponecháme v EN, status je už česky
            "mode": self.mode.value,
            "cloud_online": int(self.cloud_health.is_online),
            "cloud_queue": self.cloud_queue.size(),
            "mqtt_queue": self.mqtt_publisher.queue.size(),
            # BOX připojení (TCP) a "data tečou" jsou dvě různé věci
            "box_connected": int(self.box_tcp_connected),
            "box_connections": self._box_conn_count,
            "box_data_recent": box_data_recent,
            "last_data": self.last_data_iso,
            "isnewset_polls": self.isnewset_polls,
            "isnewset_last_poll": self.isnewset_last_poll_iso,
            "isnewset_last_response": self.isnewset_last_response,
            "isnewset_last_rtt_ms": self.isnewset_last_rtt_ms,
        }
        await self.mqtt_publisher.publish_proxy_status(payload)
        self._last_status_publish = now
    
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
        
        # Initial status publish
        await self.publish_proxy_status(force=True)
        # Periodický heartbeat stavového senzoru
        if self._status_task is None or self._status_task.done():
            self._status_task = asyncio.create_task(self._status_loop())
        
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
    
    async def _on_cloud_state_change(self, event: str):
        """Callback při změně stavu cloudu."""
        async with self.mode_lock:
            old_mode = self.mode
            
            if event == "cloud_down":
                # Zavřeme persistent cloud spojení, ať se resetuje
                await self.cloud_session.close()
                # Cloud vypadl → OFFLINE režim
                self.mode = ProxyMode.OFFLINE
                logger.warning(
                    f"🔴 Režim změněn: {old_mode.value} → {self.mode.value}"
                )
                self.stats["mode_changes"] += 1
                
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
                await self.publish_proxy_status(force=True)
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
                await self.publish_proxy_status(force=True)
                break
            
            frame_id, table_name, frame_data = item
            
            # Pošli na cloud
            try:
                ack = await self.cloud_session.send_and_read_ack(
                    frame_data.encode("utf-8"),
                    ack_timeout_s=3.0,
                    ack_max_bytes=4096,
                )
                
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

    async def _status_loop(self):
        """Heartbeat pro stavový senzor, aby se discovery/stav poslaly i bez dat z BOXu."""
        while True:
            try:
                await self.publish_proxy_status(force=True)
            except Exception as e:
                logger.debug(f"Status loop error: {e}")
            await asyncio.sleep(30)
    
    async def handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        """Handle jednoho BOX připojení - persistent connection."""
        addr = writer.get_extra_info('peername')
        logger.debug(f"🔌 BOX připojen: {addr}")
        self._ever_seen_box = True
        async with self._box_conn_lock:
            self._box_conn_count += 1
            self.box_tcp_connected = self._box_conn_count > 0
        await self.publish_proxy_status(force=True)
        
        try:
            # Zpracuj podle aktuálního režimu
            async with self.mode_lock:
                current_mode = self.mode
            
            if current_mode == ProxyMode.ONLINE:
                await self._handle_online_mode_connection(reader, writer)
            else:
                # OFFLINE nebo REPLAY → lokální ACK + queue
                await self._handle_offline_mode_connection(reader, writer)
            
        except Exception as e:
            logger.error(f"❌ Chyba při zpracování spojení od {addr}: {e}")
        finally:
            async with self._box_conn_lock:
                self._box_conn_count = max(0, self._box_conn_count - 1)
                self.box_tcp_connected = self._box_conn_count > 0
            await self.publish_proxy_status(force=True)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
    
    async def _handle_online_mode_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter
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
        CLOUD_ACK_TIMEOUT = 10.0
        
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
                        "⏱️ BOX idle timeout (15 min) - closing session"
                    )
                    break
                
                if not data:
                    logger.debug("🔌 BOX ukončil spojení (EOF)")
                    break
                
                # Zpracuj frame
                frame = data.decode('utf-8')
                self.stats["frames_received"] += 1
                
                # Parse & capture
                parsed = self.parser.parse_xml_frame(frame)
                device_id = parsed.get("_device_id") if parsed else None
                table_name = parsed.get("_table") if parsed else None
                isnewset_request = (
                    "<Result>IsNewSet</Result>" in frame
                    or (parsed and parsed.get("Result") == "IsNewSet")
                )
                isnewset_request = (
                    "<Result>IsNewSet</Result>" in frame
                    or (parsed and parsed.get("Result") == "IsNewSet")
                )
                
                # Auto-detect device_id from BOX frames
                if device_id and self.device_id == "AUTO":
                    self.device_id = device_id
                    self.mqtt_publisher.device_id = device_id
                    # Clear discovery cache to re-send with correct device_id
                    self.mqtt_publisher.discovery_sent.clear()
                    # Re-publish availability with correct device_id
                    self.mqtt_publisher.publish_availability()
                    logger.info(f"🔑 Device ID detected: {device_id}")
                    await self.publish_proxy_status(force=True)
                
                capture_payload(
                    device_id, table_name, frame, parsed or {},
                    direction="box_to_proxy", length=len(frame)
                )
                
                # MQTT publish (vždy, nezávisle na cloud)
                if parsed:
                    self.last_data_iso = iso_now()
                    self._last_data_epoch = time.time()
                    if isnewset_request:
                        self.isnewset_polls += 1
                        self.isnewset_last_poll_iso = self.last_data_iso
                        self._pending_isnewset_start = self._last_data_epoch
                        # Default until we see cloud response
                        self.isnewset_last_response = None
                        self.isnewset_last_response_iso = None
                        self.isnewset_last_rtt_ms = None
                    await self.publish_proxy_status(force=True)
                    await self.mqtt_publisher.publish_data(parsed)
                
                # Forward na cloud
                try:
                    self.stats["frames_forwarded"] += 1
                    
                    # Capture frame poslaný na cloud
                    capture_payload(
                        device_id, table_name, frame, parsed or {},
                        direction="proxy_to_cloud", length=len(frame)
                    )
                    
                    # Čekej na ACK od cloudu
                    ack_data = await self.cloud_session.send_and_read_ack(
                        data,
                        ack_timeout_s=CLOUD_ACK_TIMEOUT,
                        ack_max_bytes=4096,
                    )
                    
                    # Capture cloud response
                    ack_str = ack_data.decode('utf-8')
                    capture_payload(
                        None, table_name, ack_str, {},
                        direction="cloud_to_proxy", length=len(ack_data)
                    )

                    # IsNewSet telemetry - classify cloud response
                    if isnewset_request and self._pending_isnewset_start is not None:
                        response = "Neznámá"
                        if "<TblName>" in ack_str:
                            response = "Nastavení"
                        if "<Result>END</Result>" in ack_str and response != "Nastavení":
                            response = "END"
                        if "<Result>ACK</Result>" in ack_str and response == "Neznámá":
                            response = "ACK"
                        self.isnewset_last_response = response
                        self.isnewset_last_response_iso = iso_now()
                        self.isnewset_last_rtt_ms = int(
                            max(0.0, (time.time() - self._pending_isnewset_start) * 1000)
                        )
                        self._pending_isnewset_start = None
                        await self.publish_proxy_status(force=True)
                    
                    # ACK Learning
                    if table_name:
                        self.ack_learner.learn_from_cloud(ack_str, table_name)
                    
                    # Forward ACK na BOX
                    box_writer.write(ack_data)
                    await box_writer.drain()
                    self.stats["acks_cloud"] += 1
                    
                    # Capture ACK poslaný na BOX
                    capture_payload(
                        None, table_name, ack_str, {},
                        direction="proxy_to_box", length=len(ack_data)
                    )
                    
                except asyncio.TimeoutError:
                    logger.warning(
                        "⏱️ Cloud ACK timeout - offline mode for this frame"
                    )
                    if isnewset_request:
                        self.isnewset_last_response = "Bez odpovědi"
                        self.isnewset_last_response_iso = iso_now()
                        self.isnewset_last_rtt_ms = None
                        self._pending_isnewset_start = None
                        await self.publish_proxy_status(force=True)
                    await self._process_frame_offline(
                        frame, table_name, device_id, box_writer
                    )
                    
                except Exception as e:
                    logger.warning(
                        f"⚠️ Cloud error: {e} - offline mode for this frame"
                    )
                    if isnewset_request:
                        self.isnewset_last_response = "Bez odpovědi"
                        self.isnewset_last_response_iso = iso_now()
                        self.isnewset_last_rtt_ms = None
                        self._pending_isnewset_start = None
                        await self.publish_proxy_status(force=True)
                    await self._process_frame_offline(
                        frame, table_name, device_id, box_writer
                    )
                    
        except Exception as e:
            logger.error(f"❌ Online mode error: {e}")
        finally:
            # Cloud session je globální (nezávislá na BOX session) → nezavírat zde.
            pass
    
    async def _process_frame_offline(
        self,
        frame: str,
        table_name: str | None,
        device_id: str | None,
        box_writer: asyncio.StreamWriter
    ):
        """Zpracuj frame v offline režimu - lokální ACK + queue."""
        # Generuj lokální ACK
        ack_response = self.ack_learner.generate_ack(table_name)
        box_writer.write(ack_response.encode('utf-8'))
        await box_writer.drain()
        self.stats["acks_local"] += 1
        
        # Queue frame pro replay (kromě handshake)
        if table_name and table_name != "tbl_handshake":
            await self.cloud_queue.add(frame, table_name, device_id)
            self.stats["frames_queued"] += 1
    
    async def _handle_offline_mode_connection(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter
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
                        "⏱️ BOX idle timeout (15 min) - closing session"
                    )
                    break
                
                if not data:
                    logger.debug("🔌 BOX ukončil spojení (EOF)")
                    break
                
                frame = data.decode('utf-8')
                self.stats["frames_received"] += 1
                
                # Parse & capture
                parsed = self.parser.parse_xml_frame(frame)
                device_id = parsed.get("_device_id") if parsed else None
                table_name = parsed.get("_table") if parsed else None
                
                capture_payload(
                    device_id, table_name, frame, parsed or {},
                    direction="box_to_proxy", length=len(frame)
                )
                
                # MQTT publish
                if parsed:
                    self.last_data_iso = iso_now()
                    self._last_data_epoch = time.time()
                    if isnewset_request:
                        self.isnewset_polls += 1
                        self.isnewset_last_poll_iso = self.last_data_iso
                        self.isnewset_last_response = "Bez odpovědi (offline)"
                        self.isnewset_last_response_iso = self.last_data_iso
                        self.isnewset_last_rtt_ms = None
                        self._pending_isnewset_start = None
                    await self.publish_proxy_status(force=True)
                    await self.mqtt_publisher.publish_data(parsed)
                
                # Lokální ACK + queue
                await self._process_frame_offline(
                    frame, table_name, device_id, box_writer
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
