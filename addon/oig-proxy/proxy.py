#!/usr/bin/env python3
"""
OIG Proxy - hlavní orchestrace s ONLINE/OFFLINE/REPLAY režimy.
"""

import asyncio
import logging
import time
from typing import Any

from cloud_manager import ACKLearner, CloudQueue
from cloud_session import CloudSessionManager, CloudStats
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
        self.cloud_stats = CloudStats()
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
        self._active_box_writer: asyncio.StreamWriter | None = None
        self._active_cloud_session: CloudSessionManager | None = None
        self._active_conn_id: int | None = None
        self._session_seq = 0

        # IsNewSet telemetry (BOX → Cloud poll)
        self.isnewset_polls = 0
        self.isnewset_last_poll_iso: str | None = None
        self.isnewset_last_response: str | None = None
        self.isnewset_last_response_iso: str | None = None
        self.isnewset_last_rtt_ms: int | None = None
        self._pending_isnewset_start: float | None = None
        
        # Background tasks
        self._status_task: asyncio.Task[Any] | None = None
        self._cloud_reconnect_task: asyncio.Task[Any] | None = None
        self._cloud_reconnect_conn_id: int | None = None
        
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
        cloud_connected = int(
            self._active_cloud_session.is_connected()
            if self._active_cloud_session
            else 0
        )
        payload = {
            "status": status,
            # mode ponecháme v EN, status je už česky
            "mode": self.mode.value,
            "cloud_online": cloud_connected,
            "cloud_session_connected": cloud_connected,
            "cloud_connects": self.cloud_stats.connects,
            "cloud_disconnects": self.cloud_stats.disconnects,
            "cloud_timeouts": self.cloud_stats.timeouts,
            "cloud_errors": self.cloud_stats.errors,
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

    async def _refresh_mode(self) -> None:
        """Odvodí ProxyMode z aktuálního stavu cloudu a fronty."""
        async with self.mode_lock:
            old = self.mode
            cloud_connected = (
                self._active_cloud_session.is_connected()
                if self._active_cloud_session
                else False
            )
            if not cloud_connected:
                self.mode = ProxyMode.OFFLINE
            elif self.cloud_queue.size() > 0:
                self.mode = ProxyMode.REPLAY
            else:
                self.mode = ProxyMode.ONLINE

            changed = self.mode != old
            if changed:
                self.stats["mode_changes"] += 1

        if changed:
            await self.publish_proxy_status(force=True)

    async def _replay_cloud_queue_some(
        self,
        cloud_session: CloudSessionManager,
        *,
        max_frames: int = 1,
        ack_timeout_s: float = 3.0,
    ) -> int:
        """Pošle max. N frames z CloudQueue na cloud (best-effort)."""
        if max_frames <= 0:
            return 0

        replayed = 0
        for _ in range(max_frames):
            item = await self.cloud_queue.get_next()
            if not item:
                break

            frame_id, table_name, frame_data = item
            try:
                ack_data = await cloud_session.send_and_read_ack(
                    frame_data.encode("utf-8"),
                    ack_timeout_s=ack_timeout_s,
                    ack_max_bytes=4096,
                )
                ack_str = ack_data.decode("utf-8", errors="replace")
                capture_payload(
                    None,
                    table_name,
                    ack_str,
                    {},
                    direction="cloud_to_proxy",
                    length=len(ack_data),
                )
                if table_name:
                    self.ack_learner.learn_from_cloud(ack_str, table_name)
                await self.cloud_queue.remove(frame_id)
                replayed += 1
            except Exception:
                # Cloud znovu selhal - necháme frame ve frontě.
                await self._refresh_mode()
                break

            # Rate limiting (jen pokud posíláme víc než 1 frame)
            if max_frames > 1 and CLOUD_REPLAY_RATE > 0:
                await asyncio.sleep(1.0 / CLOUD_REPLAY_RATE)

        return replayed

    def _ensure_cloud_reconnect_task(
        self,
        cloud_session: CloudSessionManager,
        *,
        conn_id: int,
    ) -> None:
        """Spustí background reconnect, aby OFFLINE nezdržoval ACKy BOXu."""
        task = self._cloud_reconnect_task
        if task is not None and not task.done():
            if self._cloud_reconnect_conn_id == conn_id:
                return
            task.cancel()

        self._cloud_reconnect_conn_id = conn_id
        self._cloud_reconnect_task = asyncio.create_task(
            self._cloud_reconnect_loop(cloud_session, conn_id),
        )

    async def _cloud_reconnect_loop(
        self,
        cloud_session: CloudSessionManager,
        conn_id: int,
    ) -> None:
        """Opakovaně zkouší obnovit cloud session pro aktivní BOX spojení."""
        try:
            while True:
                async with self._box_conn_lock:
                    still_active = (
                        self._active_cloud_session is cloud_session
                        and self._active_box_writer is not None
                        and self._cloud_reconnect_conn_id == conn_id
                    )
                if not still_active:
                    return

                try:
                    await cloud_session.ensure_connected()
                    await self._refresh_mode()
                    await self.publish_proxy_status(force=True)
                    return
                except Exception:
                    await self._refresh_mode()
                    # ensure_connected má vlastní backoff; zde jen zabráníme těsnému loopu
                    await asyncio.sleep(1.0)
        finally:
            # Uklidit jen pokud stále ukazujeme na tento task
            if self._cloud_reconnect_conn_id == conn_id:
                self._cloud_reconnect_task = None
                self._cloud_reconnect_conn_id = None

    async def _send_local_ack(
        self,
        table_name: str | None,
        ack_key: str | None,
        box_writer: asyncio.StreamWriter,
    ) -> None:
        ack = self.ack_learner.generate_ack(ack_key or table_name)
        try:
            box_writer.write(ack.encode("utf-8"))
            await box_writer.drain()
            self.stats["acks_local"] += 1
        except Exception:
            pass

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
        previous_writer: asyncio.StreamWriter | None = None
        previous_cloud: CloudSessionManager | None = None
        previous_reconnect_task: asyncio.Task[Any] | None = None
        async with self._box_conn_lock:
            self._box_conn_count += 1
            self.box_tcp_connected = self._box_conn_count > 0

            # Standardní session logika:
            # - při novém BOX spojení ukončit staré BOX + CLOUD spojení
            previous_writer = self._active_box_writer
            previous_cloud = self._active_cloud_session
            previous_reconnect_task = self._cloud_reconnect_task
            self._cloud_reconnect_task = None
            self._cloud_reconnect_conn_id = None

            self._session_seq += 1
            conn_id = self._session_seq
            self._active_conn_id = conn_id

            self._active_box_writer = writer
            cloud_session = CloudSessionManager(
                TARGET_SERVER,
                TARGET_PORT,
                stats=self.cloud_stats,
                connect_timeout_s=2.0,
            )
            self._active_cloud_session = cloud_session

        # Zavři předchozí spojení mimo lock (best-effort)
        if previous_reconnect_task is not None and not previous_reconnect_task.done():
            previous_reconnect_task.cancel()
        if previous_writer is not None and previous_writer is not writer:
            try:
                previous_writer.close()
            except Exception:
                pass
        if previous_cloud is not None and previous_cloud is not cloud_session:
            try:
                await previous_cloud.close(count_disconnect=True)
            except Exception:
                pass

        # Nové BOX spojení → reset cloud session; připojení řeší background reconnect (neblokuje BOX).
        self._ensure_cloud_reconnect_task(cloud_session, conn_id=conn_id)
        await self._refresh_mode()
        await self.publish_proxy_status(force=True)
        
        try:
            await self._handle_box_session(reader, writer, cloud_session, conn_id, addr)
            
        except Exception as e:
            logger.error(f"❌ Chyba při zpracování spojení od {addr}: {e}")
        finally:
            try:
                await cloud_session.close(count_disconnect=True)
            except Exception:
                pass

            async with self._box_conn_lock:
                reconnect_task = self._cloud_reconnect_task
                reconnect_id = self._cloud_reconnect_conn_id
                if reconnect_task is not None and not reconnect_task.done() and reconnect_id == conn_id:
                    reconnect_task.cancel()
                self._box_conn_count = max(0, self._box_conn_count - 1)
                self.box_tcp_connected = self._box_conn_count > 0
                if self._active_box_writer is writer:
                    self._active_box_writer = None
                if self._active_cloud_session is cloud_session:
                    self._active_cloud_session = None
                if self._active_conn_id == conn_id:
                    self._active_conn_id = None

            await self._refresh_mode()
            await self.publish_proxy_status(force=True)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_box_session(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter,
        cloud_session: CloudSessionManager,
        conn_id: int,
        peer: Any,
    ):
        """
        Standardní BOX ↔ Cloud proxy session:
        - Jedno BOX spojení má jednu cloud session (vytvořeno na connectu).
        - V ONLINE: frame se pošle na cloud a cloud ACK se vrátí BOXu.
        - Když cloud spadne/ukončí session, BOX držíme a další frame zkusí nový cloud connect.
        - V OFFLINE: posíláme lokální ACK a frame ukládáme do CloudQueue pro pozdější replay.
        """
        BOX_IDLE_TIMEOUT = 900  # 15 minut
        CLOUD_ACK_TIMEOUT = 3.0
        
        try:
            while True:
                # Pokud nás mezitím nahradilo nové BOX spojení, ukonči tuto session.
                async with self._box_conn_lock:
                    if self._active_conn_id != conn_id:
                        return

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
                frame = data.decode('utf-8', errors='replace')
                self.stats["frames_received"] += 1
                
                # Parse & capture
                parsed = self.parser.parse_xml_frame(frame)
                device_id = parsed.get("_device_id") if parsed else None
                table_name = parsed.get("_table") if parsed else None
                isnewset_request = (
                    "<Result>IsNewSet</Result>" in frame
                    or (parsed and parsed.get("Result") == "IsNewSet")
                )
                isnewset_key = parsed.get("Result") if parsed else None
                
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
                    direction="box_to_proxy", conn_id=conn_id, peer=str(peer), length=len(data)
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

                # Cloud OFFLINE: neblokujeme BOX čekáním na reconnect, jen ACK + queue.
                if not cloud_session.is_connected():
                    if self._pending_isnewset_start is not None:
                        self.isnewset_last_response = "Bez odpovědi (offline)"
                        self.isnewset_last_response_iso = iso_now()
                        self.isnewset_last_rtt_ms = None
                        self._pending_isnewset_start = None
                        await self.publish_proxy_status(force=True)

                    await self._send_local_ack(
                        table_name,
                        isnewset_key if isnewset_request else None,
                        box_writer,
                    )
                    if table_name and table_name != "tbl_handshake":
                        await self.cloud_queue.add(frame, table_name, device_id)
                        self.stats["frames_queued"] += 1
                        await self._refresh_mode()

                    self._ensure_cloud_reconnect_task(cloud_session, conn_id=conn_id)
                    continue

                # Forward do cloudu (nebo offline fallback)
                try:
                    self.stats["frames_forwarded"] += 1
                    capture_payload(
                        device_id, table_name, frame, parsed or {},
                        direction="proxy_to_cloud", conn_id=conn_id, peer=str(peer), length=len(data)
                    )
                    ack_data = await cloud_session.send_and_read_ack(
                        data,
                        ack_timeout_s=CLOUD_ACK_TIMEOUT,
                        ack_max_bytes=4096,
                    )
                    await self._refresh_mode()

                    ack_str = ack_data.decode("utf-8", errors="replace")
                    capture_payload(
                        None, table_name, ack_str, {},
                        direction="cloud_to_proxy", conn_id=conn_id, peer=str(peer), length=len(ack_data)
                    )

                    if self._pending_isnewset_start is not None:
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

                    if table_name:
                        self.ack_learner.learn_from_cloud(ack_str, table_name)

                    try:
                        box_writer.write(ack_data)
                        await box_writer.drain()
                        self.stats["acks_cloud"] += 1
                    except Exception:
                        pass
                    capture_payload(
                        None, table_name, ack_str, {},
                        direction="proxy_to_box", conn_id=conn_id, peer=str(peer), length=len(ack_data)
                    )

                    # Replay backlog až po odeslání ACK BOXu (abychom nezdržovali odpovědi BOXu).
                    if self.cloud_queue.size() > 0:
                        await self._replay_cloud_queue_some(
                            cloud_session,
                            max_frames=1,
                            ack_timeout_s=CLOUD_ACK_TIMEOUT,
                        )
                        await self._refresh_mode()
                except Exception:
                    # Cloud není dostupný / spadlo spojení → OFFLINE fallback pro tento frame
                    await self._refresh_mode()

                    if self._pending_isnewset_start is not None:
                        self.isnewset_last_response = "Bez odpovědi"
                        self.isnewset_last_response_iso = iso_now()
                        self.isnewset_last_rtt_ms = None
                        self._pending_isnewset_start = None
                        await self.publish_proxy_status(force=True)

                    # Lokální ACK: pro IsNewSet použij END pattern
                    await self._send_local_ack(
                        table_name,
                        isnewset_key if isnewset_request else None,
                        box_writer,
                    )

                    # Persist do fronty pro replay (kromě handshake)
                    if table_name and table_name != "tbl_handshake":
                        await self.cloud_queue.add(frame, table_name, device_id)
                        self.stats["frames_queued"] += 1
                        await self._refresh_mode()

                    self._ensure_cloud_reconnect_task(cloud_session, conn_id=conn_id)
                    
        except Exception as e:
            logger.error(f"❌ Online mode error: {e}")
        finally:
            # Cloud session je svázaná s BOX session - ukončí se v handle_connection().
            pass
    
    def get_stats(self) -> dict[str, Any]:
        """Vrátí statistiky proxy."""
        return {
            "mode": self.mode.value,
            "cloud_online": (
                self._active_cloud_session.is_connected()
                if self._active_cloud_session
                else False
            ),
            "cloud_queue_size": self.cloud_queue.size(),
            "mqtt_queue_size": self.mqtt_publisher.queue.size(),
            "mqtt_connected": self.mqtt_publisher.connected,
            **self.stats
        }
