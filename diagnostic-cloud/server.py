#!/usr/bin/env python3
"""
OIG Diagnostic Cloud Server.

Jednoduchý mock server simulující oigservis.cz:5710.
Zachytává a loguje veškerou komunikaci od proxy/BOX.

Určeno pro diagnostiku problémových instalací.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Configuration
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))
PORT = int(os.environ.get("PORT", "5710"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Setup logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("oig-diagnostic")


@dataclass
class ClientInfo:
    """Informace o připojeném klientovi."""
    device_id: str
    first_seen: str
    last_seen: str
    client_ips: set = field(default_factory=set)
    client_ports: set = field(default_factory=set)  # Track source ports
    total_connections: int = 0
    total_frames: int = 0
    total_bytes_rx: int = 0
    total_bytes_tx: int = 0
    tables_seen: set = field(default_factory=set)
    
    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "client_ips": list(self.client_ips),
            "client_ports_seen": len(self.client_ports),  # How many unique source ports
            "total_connections": self.total_connections,
            "total_frames": self.total_frames,
            "total_bytes_rx": self.total_bytes_rx,
            "total_bytes_tx": self.total_bytes_tx,
            "tables_seen": list(self.tables_seen)
        }


class DiagnosticCloudServer:
    """Mock OIG Cloud server pro diagnostiku."""
    
    def __init__(self):
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self._server: Optional[asyncio.Server] = None
        self._running = False
        self._connection_counter = 0
        
        # Client tracking
        self._clients: dict[str, ClientInfo] = {}
        self._load_clients()
        
        # Stats
        self._start_time = datetime.now()
        self._total_frames = 0
        
    def _load_clients(self):
        """Load existing client data."""
        clients_file = self.data_dir / "clients.json"
        if clients_file.exists():
            try:
                with open(clients_file, 'r') as f:
                    data = json.load(f)
                for device_id, info in data.items():
                    self._clients[device_id] = ClientInfo(
                        device_id=info["device_id"],
                        first_seen=info["first_seen"],
                        last_seen=info["last_seen"],
                        client_ips=set(info.get("client_ips", [])),
                        client_ports=set(),  # Not persisted, reset on restart
                        total_connections=info.get("total_connections", 0),
                        total_frames=info.get("total_frames", 0),
                        total_bytes_rx=info.get("total_bytes_rx", 0),
                        total_bytes_tx=info.get("total_bytes_tx", 0),
                        tables_seen=set(info.get("tables_seen", []))
                    )
                logger.info(f"Loaded {len(self._clients)} existing clients")
            except Exception as e:
                logger.warning(f"Could not load clients: {e}")
                
    def _save_clients(self):
        """Save client data."""
        clients_file = self.data_dir / "clients.json"
        data = {k: v.to_dict() for k, v in self._clients.items()}
        with open(clients_file, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
    async def start(self):
        """Start server."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            "0.0.0.0",
            PORT,
            reuse_address=True
        )
        self._running = True
        
        logger.info("=" * 60)
        logger.info("OIG DIAGNOSTIC CLOUD SERVER")
        logger.info("=" * 60)
        logger.info(f"Listening on port {PORT}")
        logger.info(f"Data directory: {self.data_dir}")
        logger.info(f"Retention: {RETENTION_DAYS} days")
        logger.info(f"Known clients: {len(self._clients)}")
        logger.info("=" * 60)
        
        # Start cleanup task
        asyncio.create_task(self._periodic_cleanup())
        asyncio.create_task(self._periodic_save())
        
    async def stop(self):
        """Stop server."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self._save_clients()
        logger.info("Server stopped")
        
    async def run_forever(self):
        """Run until interrupted."""
        await self.start()
        
        loop = asyncio.get_event_loop()
        stop_event = asyncio.Event()
        
        def handle_signal():
            stop_event.set()
            
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, handle_signal)
            
        await stop_event.wait()
        await self.stop()
        
    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        """Handle incoming connection."""
        self._connection_counter += 1
        conn_id = self._connection_counter
        
        # Connection info
        peer = writer.get_extra_info('peername')
        client_ip = peer[0] if peer else "unknown"
        client_port = peer[1] if peer else 0
        
        # Session tracking
        session_start = time.time()
        last_frame_time = session_start
        bytes_received = 0
        bytes_sent = 0
        frame_gaps = []  # Inter-frame delays
        
        logger.info(f"[{conn_id}] Connection from {client_ip}:{client_port}")
        
        device_id = None
        frames_in_connection = 0
        close_reason = "normal"
        
        try:
            while self._running:
                try:
                    data = await asyncio.wait_for(reader.read(8192), timeout=120.0)
                    if not data:
                        break
                except asyncio.TimeoutError:
                    close_reason = "timeout"
                    break
                except ConnectionResetError:
                    close_reason = "reset"
                    break
                    
                # Timing
                now = time.time()
                if frames_in_connection > 0:
                    gap = now - last_frame_time
                    frame_gaps.append(gap)
                last_frame_time = now
                
                bytes_received += len(data)
                frame = data.decode('utf-8', errors='ignore')
                self._total_frames += 1
                frames_in_connection += 1
                
                # Parse frame
                parsed = self._parse_frame(frame)
                frame_device_id = parsed.get("device_id")
                table_name = parsed.get("table_name", "unknown")
                
                # Track device
                if frame_device_id and frame_device_id != "0000000000":
                    device_id = frame_device_id
                    self._track_client(device_id, client_ip, client_port, table_name)
                    self._save_frame(device_id, frame, parsed, client_ip, client_port, conn_id)
                    
                # Log
                logger.info(
                    f"[{conn_id}] {table_name:15} Device={device_id or '?'} "
                    f"Size={len(frame)}"
                )
                
                # Send ACK
                await asyncio.sleep(0.01)  # 10ms delay like real cloud
                ack = self._generate_ack(parsed)
                writer.write(ack.encode('utf-8'))
                await writer.drain()
                bytes_sent += len(ack)
                
        except Exception as e:
            close_reason = f"error:{type(e).__name__}"
            logger.error(f"[{conn_id}] Error: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            
            # Session stats
            session_duration = time.time() - session_start
            avg_gap = sum(frame_gaps) / len(frame_gaps) if frame_gaps else 0
            
            logger.info(
                f"[{conn_id}] Closed. Frames={frames_in_connection} "
                f"Device={device_id or '?'} Duration={session_duration:.1f}s "
                f"Rx={bytes_received}B Tx={bytes_sent}B AvgGap={avg_gap*1000:.0f}ms "
                f"Reason={close_reason}"
            )
            
            # Save session summary
            if device_id:
                self._track_session_end(device_id, bytes_received, bytes_sent)
                self._save_session(device_id, {
                    "conn_id": conn_id,
                    "client_ip": client_ip,
                    "client_port": client_port,
                    "start": datetime.fromtimestamp(session_start).isoformat(),
                    "duration_sec": round(session_duration, 2),
                    "frames": frames_in_connection,
                    "bytes_rx": bytes_received,
                    "bytes_tx": bytes_sent,
                    "avg_gap_ms": round(avg_gap * 1000, 1),
                    "close_reason": close_reason
                })
                self._save_clients()
            
    def _parse_frame(self, frame: str) -> dict:
        """Parse OIG frame."""
        result = {}
        
        patterns = {
            "table_name": r'<TblName>([^<]+)</TblName>',
            "result": r'<Result>([^<]+)</Result>',
            "device_id": r'<ID_Device>(\d+)</ID_Device>',
            "id_set": r'<ID_Set>(\d+)</ID_Set>',
            "timestamp": r'<DT>([^<]+)</DT>',
            "reason": r'<Reason>([^<]+)</Reason>',
        }
        
        for key, pattern in patterns.items():
            match = re.search(pattern, frame)
            if match:
                result[key] = match.group(1)
                
        return result
        
    def _track_client(self, device_id: str, client_ip: str, client_port: int, table_name: str):
        """Track client activity per frame."""
        now = datetime.now().isoformat()
        
        if device_id not in self._clients:
            self._clients[device_id] = ClientInfo(
                device_id=device_id,
                first_seen=now,
                last_seen=now
            )
            logger.info(f"NEW CLIENT: {device_id} from {client_ip}")
            
        client = self._clients[device_id]
        client.last_seen = now
        client.client_ips.add(client_ip)
        client.client_ports.add(client_port)
        client.total_frames += 1
        client.tables_seen.add(table_name)
        
    def _track_session_end(self, device_id: str, bytes_rx: int, bytes_tx: int):
        """Track session statistics at disconnect."""
        if device_id in self._clients:
            client = self._clients[device_id]
            client.total_connections += 1
            client.total_bytes_rx += bytes_rx
            client.total_bytes_tx += bytes_tx
            
    def _save_frame(self, device_id: str, frame: str, parsed: dict, client_ip: str, client_port: int, conn_id: int):
        """Save frame to file."""
        # Create device directory
        device_dir = self.data_dir / "frames" / device_id
        device_dir.mkdir(parents=True, exist_ok=True)
        
        # Daily file
        today = datetime.now().strftime("%Y-%m-%d")
        daily_file = device_dir / f"{today}.jsonl"
        
        record = {
            "ts": datetime.now().isoformat(),
            "conn": conn_id,
            "ip": client_ip,
            "port": client_port,
            "table": parsed.get("table_name", "unknown"),
            "size": len(frame),
            "raw": frame  # Full frame - no truncation
        }
        
        with open(daily_file, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            
    def _save_session(self, device_id: str, session_info: dict):
        """Save session summary."""
        device_dir = self.data_dir / "sessions" / device_id
        device_dir.mkdir(parents=True, exist_ok=True)
        
        today = datetime.now().strftime("%Y-%m-%d")
        sessions_file = device_dir / f"{today}.jsonl"
        
        with open(sessions_file, 'a') as f:
            f.write(json.dumps(session_info, ensure_ascii=False) + "\n")
            
    def _generate_ack(self, parsed: dict) -> str:
        """Generate ACK response."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if parsed.get("result") == "END":
            return f'<Frame><Result>ACK</Result><ToDo>GetAll</ToDo><Time>{now}</Time><CRC>99999</CRC></Frame>\r\n'
        else:
            return f'<Frame><Result>ACK</Result><ToDo>GetAll</ToDo><Time>{now}</Time><CRC>12345</CRC></Frame>\r\n'
            
    async def _periodic_cleanup(self):
        """Cleanup old data."""
        while self._running:
            await asyncio.sleep(3600)  # Every hour
            
            try:
                cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
                frames_dir = self.data_dir / "frames"
                
                if not frames_dir.exists():
                    continue
                    
                deleted = 0
                for device_dir in frames_dir.iterdir():
                    if not device_dir.is_dir():
                        continue
                    for daily_file in device_dir.glob("*.jsonl"):
                        # Parse date from filename
                        try:
                            file_date = datetime.strptime(daily_file.stem, "%Y-%m-%d")
                            if file_date < cutoff:
                                daily_file.unlink()
                                deleted += 1
                        except ValueError:
                            pass
                            
                if deleted:
                    logger.info(f"Cleanup: deleted {deleted} old files")
                    
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
                
    async def _periodic_save(self):
        """Periodically save client data."""
        while self._running:
            await asyncio.sleep(300)  # Every 5 minutes
            self._save_clients()


async def main():
    server = DiagnosticCloudServer()
    await server.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped")
