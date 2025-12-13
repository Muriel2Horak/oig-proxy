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
    TARGET_PORT,
    TARGET_SERVER,
)
from models import ProxyMode
from mqtt_publisher import MQTTPublisher
from parser import OIGDataParser
from utils import capture_payload

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
        
        # Režim
        self.mode = ProxyMode.ONLINE
        self.mode_lock = asyncio.Lock()
        
        # Background tasky
        self._replay_task: asyncio.Task[Any] | None = None
        
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

        # Po připojení MQTT publikuj stav (init)
        await self.publish_proxy_status(force=True)
        
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
            "cloud_online": int(self.cloud_health.is_online),
            "cloud_connects": None,
            "cloud_disconnects": None,
            "cloud_timeouts": None,
            "cloud_errors": None,
            "cloud_queue": self.cloud_queue.size(),
            "mqtt_queue": self.mqtt_publisher.queue.size(),
            "box_connected": int(self.box_connected),
            "box_connections": self.box_connections,
            "box_data_recent": int(
                self._last_data_epoch is not None
                and (time.time() - self._last_data_epoch) <= 90
            ),
            "last_data": self._last_data_iso,
        }
        return payload

    async def publish_proxy_status(self, *, force: bool = False) -> None:
        """Publikuje stav proxy."""
        payload = self._build_status_payload()
        try:
            await self.mqtt_publisher.publish_proxy_status(payload)
        except Exception as e:
            logger.debug(f"Proxy status publish failed: {e}")
    
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
        logger.debug(f"🔌 BOX připojen: {addr}")
        self.box_connected = True
        self.box_connections += 1
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
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            self.box_connected = False
            await self.publish_proxy_status(force=True)
    
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
                        "⏱️ BOX idle timeout (15 min) - closing session"
                    )
                    break
                
                if not data:
                    logger.debug("🔌 BOX ukončil spojení (EOF)")
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
                
                # Auto-detect device_id from BOX frames
                if device_id and self.device_id == "AUTO":
                    self.device_id = device_id
                    self.mqtt_publisher.device_id = device_id
                    # Clear discovery cache to re-send with correct device_id
                    self.mqtt_publisher.discovery_sent.clear()
                    # Re-publish availability with correct device_id
                    self.mqtt_publisher.publish_availability()
                    logger.info(f"🔑 Device ID detected: {device_id}")
                
                capture_payload(
                    device_id, table_name, frame, parsed or {},
                    direction="box_to_proxy", length=len(frame)
                )
                
                # MQTT publish (vždy, nezávisle na cloud)
                if parsed:
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
                        logger.debug(
                            f"☁️ Připojeno k {TARGET_SERVER}:{TARGET_PORT}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"⚠️ Cloud nedostupný: {e} - offline mode"
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
                            "⚠️ Cloud ukončil spojení - reconnect next frame"
                        )
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
                        direction="cloud_to_proxy", length=len(ack_data)
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
                        "⏱️ Cloud ACK timeout - offline mode for this frame"
                    )
                    if cloud_writer:
                        cloud_writer.close()
                    cloud_writer = None
                    await self._process_frame_offline(
                        data, table_name, device_id, box_writer
                    )
                    
                except Exception as e:
                    logger.warning(
                        f"⚠️ Cloud error: {e} - offline mode for this frame"
                    )
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
                
                frame = data.decode('utf-8', errors='replace')
                self.stats["frames_received"] += 1
                self._last_data_iso = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
                self._last_data_epoch = time.time()
                
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
