#!/usr/bin/env python3
"""
OIG Proxy - hlavní orchestrace s ONLINE/OFFLINE/REPLAY režimy.
"""

import asyncio
import logging
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
        
        # Background tasks
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
            
            frame_id, table_name, frame_data = item
            
            # Pošli na cloud
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
                    timeout=5.0
                )
                
                # Pošli frame
                writer.write(frame_data.encode('utf-8'))
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
        """Handle jednoho BOX připojení."""
        addr = writer.get_extra_info('peername')
        logger.debug(f"🔌 BOX připojen: {addr}")
        
        try:
            # Přečti frame od BOX
            data = await asyncio.wait_for(reader.read(8192), timeout=10.0)
            if not data:
                return
            
            frame = data.decode('utf-8')
            self.stats["frames_received"] += 1
            
            # Parse frame pro capture
            parsed = self.parser.parse_xml_frame(frame)
            device_id = parsed.get("ID_Dev") if parsed else None
            table_name = parsed.get("_table") if parsed else None
            
            # Capture frame do DB
            capture_payload(
                device_id, table_name, frame, parsed or {},
                direction="box_to_proxy", length=len(frame)
            )
            
            # Zpracuj podle aktuálního režimu
            async with self.mode_lock:
                current_mode = self.mode
            
            if current_mode == ProxyMode.ONLINE:
                await self._handle_online_mode(frame, writer)
            else:
                # OFFLINE nebo REPLAY → lokální ACK + queue
                await self._handle_offline_or_replay_mode(frame, writer)
            
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ Timeout při čtení od {addr}")
        except Exception as e:
            logger.error(f"❌ Chyba při zpracování spojení od {addr}: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
    
    async def _handle_online_mode(
        self,
        frame: str,
        box_writer: asyncio.StreamWriter
    ):
        """ONLINE režim - transparentní forward na cloud."""
        try:
            # Připoj na cloud
            cloud_reader, cloud_writer = await asyncio.wait_for(
                asyncio.open_connection(TARGET_SERVER, TARGET_PORT),
                timeout=5.0
            )
            
            # Forward frame na cloud
            cloud_writer.write(frame.encode('utf-8'))
            await cloud_writer.drain()
            self.stats["frames_forwarded"] += 1
            
            # Čekej na ACK od cloudu
            cloud_response = await asyncio.wait_for(
                cloud_reader.read(4096),
                timeout=5.0
            )
            
            cloud_writer.close()
            await cloud_writer.wait_closed()
            
            # Capture cloud response
            response_str = cloud_response.decode('utf-8')
            capture_payload(
                None, None, response_str, {},
                direction="cloud_to_proxy", length=len(response_str)
            )
            
            # Parse table name pro učení ACK patterns
            parsed = self.parser.parse_xml_frame(frame)
            if parsed:
                table_name = parsed.get("_table")
                self.ack_learner.learn_from_cloud(response_str, table_name)
            
            # Forward ACK zpět na BOX
            box_writer.write(cloud_response)
            await box_writer.drain()
            self.stats["acks_cloud"] += 1
            
            # Parse a publish na MQTT
            if parsed:
                await self.mqtt_publisher.publish_data(parsed)
            
        except Exception as e:
            logger.error(f"❌ ONLINE mode forward failed: {e}")
            # Fallback → lokální ACK
            ack = self.ack_learner.generate_ack(None)
            box_writer.write(ack.encode('utf-8'))
            await box_writer.drain()
            self.stats["acks_local"] += 1
    
    async def _handle_offline_or_replay_mode(
        self,
        frame: str,
        box_writer: asyncio.StreamWriter
    ):
        """OFFLINE/REPLAY režim - lokální ACK + queue frame."""
        # Parse frame
        parsed = self.parser.parse_xml_frame(frame)
        table_name = parsed.get("_table") if parsed else None
        
        # Generuj lokální ACK
        ack = self.ack_learner.generate_ack(table_name)
        box_writer.write(ack.encode('utf-8'))
        await box_writer.drain()
        self.stats["acks_local"] += 1
        
        # Capture ACK
        capture_payload(
            None, table_name, ack, {},
            direction="proxy_to_box", length=len(ack)
        )
        
        # Přidej frame do cloud fronty (FIFO - append na konec)
        device_id = parsed.get("ID_Dev") if parsed else None
        await self.cloud_queue.add(frame, table_name or "unknown", device_id)
        self.stats["frames_queued"] += 1
        
        # Publish na MQTT (pokud je připojeno)
        if parsed:
            await self.mqtt_publisher.publish_data(parsed)
        
        # Log každých 10 frames
        if self.stats["frames_queued"] % 10 == 0:
            queue_size = self.cloud_queue.size()
            logger.info(
                f"📦 {self.mode.value}: {self.stats['frames_queued']} "
                f"frames queued ({queue_size} ve frontě)"
            )
    
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
