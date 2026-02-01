#!/usr/bin/env python3
"""
OIG Diagnostic Cloud Server with Telemetry API.

Ports:
- 5710: OIG Protocol (mock oigservis.cz)
- 5720: Telemetry API (provisioning, metrics)
- 8080: Web Dashboard

Features:
- SQLite database for tokens and telemetry
- Auto-provisioning with secure tokens
- Dashboard with live activity
"""

import asyncio
import json
import logging
import os
import re
import secrets
import signal
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import local_oig_crc

# Configuration
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))
PORT = int(os.environ.get("PORT", "5710"))
WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))
API_PORT = int(os.environ.get("API_PORT", "5720"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
TOKEN_EXPIRY_DAYS = int(os.environ.get("TOKEN_EXPIRY_DAYS", "365"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("oig-diagnostic")


class TelemetryDB:
    """SQLite database for tokens and telemetry."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
        
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                instance_hash TEXT,
                version TEXT,
                created_at TEXT NOT NULL,
                last_seen TEXT,
                last_ip TEXT,
                provisioning_count INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                uptime_s INTEGER,
                box_connected INTEGER,
                frames_received INTEGER,
                cloud_connects INTEGER,
                cloud_errors INTEGER,
                cloud_timeouts INTEGER,
                mqtt_ok INTEGER,
                mqtt_fail INTEGER,
                mode TEXT,
                version TEXT,
                box_peer TEXT,
                raw_json TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_telemetry_device_time 
            ON telemetry(device_id, timestamp)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details TEXT
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"Database initialized: {self.db_path}")
        
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
        
    def get_device(self, device_id: str) -> Optional[dict]:
        conn = self._conn()
        row = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        conn.close()
        return dict(row) if row else None
        
    def get_device_by_token(self, token: str) -> Optional[dict]:
        conn = self._conn()
        row = conn.execute("SELECT * FROM devices WHERE token = ?", (token,)).fetchone()
        conn.close()
        return dict(row) if row else None
        
    def register_device(self, device_id: str, instance_hash: str, version: str, client_ip: str) -> str:
        now = datetime.utcnow().isoformat()
        conn = self._conn()
        
        existing = conn.execute(
            "SELECT token, provisioning_count FROM devices WHERE device_id = ?",
            (device_id,)
        ).fetchone()
        
        if existing:
            new_count = existing["provisioning_count"] + 1
            conn.execute("""
                UPDATE devices SET instance_hash=?, version=?, last_seen=?, last_ip=?, provisioning_count=?
                WHERE device_id=?
            """, (instance_hash, version, now, client_ip, new_count, device_id))
            token = existing["token"]
            logger.info(f"Device re-provisioned: {device_id} (count={new_count})")
        else:
            token = secrets.token_urlsafe(32)
            conn.execute("""
                INSERT INTO devices (device_id, token, instance_hash, version, created_at, last_seen, last_ip)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (device_id, token, instance_hash, version, now, now, client_ip))
            logger.info(f"New device registered: {device_id}")
            
        conn.commit()
        conn.close()
        return token
        
    def update_device_seen(self, device_id: str, ip: str):
        conn = self._conn()
        conn.execute("UPDATE devices SET last_seen=?, last_ip=? WHERE device_id=?",
                     (datetime.utcnow().isoformat(), ip, device_id))
        conn.commit()
        conn.close()
        
    def store_telemetry(self, device_id: str, data: dict):
        now = datetime.utcnow().isoformat()
        conn = self._conn()
        conn.execute("""
            INSERT INTO telemetry (device_id, timestamp, uptime_s, box_connected, frames_received,
                cloud_connects, cloud_errors, cloud_timeouts, mqtt_ok, mqtt_fail, mode, version, box_peer, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (device_id, now, data.get("uptime_s"), 1 if data.get("box_connected") else 0,
              data.get("frames_received"), data.get("cloud_connects"), data.get("cloud_errors"),
              data.get("cloud_timeouts"), data.get("mqtt_ok"), data.get("mqtt_fail"),
              data.get("mode"), data.get("version"), data.get("box_peer"), json.dumps(data)))
        conn.commit()
        conn.close()
        
    def store_event(self, device_id: str, event_type: str, details: str = None):
        conn = self._conn()
        conn.execute("INSERT INTO events (device_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                     (device_id, datetime.utcnow().isoformat(), event_type, details))
        conn.commit()
        conn.close()
        
    def get_all_devices(self) -> list:
        conn = self._conn()
        rows = conn.execute("""
            SELECT device_id, version, created_at, last_seen, last_ip, provisioning_count
            FROM devices ORDER BY last_seen DESC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]
        
    def get_device_telemetry(self, device_id: str, limit: int = 100) -> list:
        conn = self._conn()
        rows = conn.execute("SELECT * FROM telemetry WHERE device_id=? ORDER BY timestamp DESC LIMIT ?",
                            (device_id, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
        
    def cleanup_old_telemetry(self, days: int):
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        conn = self._conn()
        cursor = conn.execute("DELETE FROM telemetry WHERE timestamp < ?", (cutoff,))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted:
            logger.info(f"Cleaned up {deleted} old telemetry records")


@dataclass
class ClientInfo:
    device_id: str
    first_seen: str
    last_seen: str
    client_ips: set = field(default_factory=set)
    client_ports: set = field(default_factory=set)
    total_connections: int = 0
    total_frames: int = 0
    total_bytes_rx: int = 0
    total_bytes_tx: int = 0
    tables_seen: set = field(default_factory=set)
    
    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id, "first_seen": self.first_seen, "last_seen": self.last_seen,
            "client_ips": list(self.client_ips), "client_ports_seen": len(self.client_ports),
            "total_connections": self.total_connections, "total_frames": self.total_frames,
            "total_bytes_rx": self.total_bytes_rx, "total_bytes_tx": self.total_bytes_tx,
            "tables_seen": list(self.tables_seen)
        }


class DiagnosticCloudServer:
    def __init__(self):
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = TelemetryDB(self.data_dir / "telemetry.db")
        self._server = None
        self._web_server = None
        self._api_server = None
        self._running = False
        self._connection_counter = 0
        self._clients: dict[str, ClientInfo] = {}
        self._load_clients()
        self._activity_log: list = []
        self._start_time = datetime.now()
        self._total_frames = 0
        
    def _load_clients(self):
        clients_file = self.data_dir / "clients.json"
        if clients_file.exists():
            try:
                with open(clients_file, 'r') as f:
                    data = json.load(f)
                for device_id, info in data.items():
                    self._clients[device_id] = ClientInfo(
                        device_id=info["device_id"], first_seen=info["first_seen"], last_seen=info["last_seen"],
                        client_ips=set(info.get("client_ips", [])), client_ports=set(),
                        total_connections=info.get("total_connections", 0), total_frames=info.get("total_frames", 0),
                        total_bytes_rx=info.get("total_bytes_rx", 0), total_bytes_tx=info.get("total_bytes_tx", 0),
                        tables_seen=set(info.get("tables_seen", []))
                    )
                logger.info(f"Loaded {len(self._clients)} existing clients")
            except Exception as e:
                logger.warning(f"Could not load clients: {e}")
                
    def _save_clients(self):
        clients_file = self.data_dir / "clients.json"
        data = {k: v.to_dict() for k, v in self._clients.items()}
        with open(clients_file, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
    def _add_activity(self, event: dict):
        self._activity_log.append(event)
        if len(self._activity_log) > 200:
            self._activity_log = self._activity_log[-200:]
            
    async def start(self):
        self._server = await asyncio.start_server(self._handle_connection, "0.0.0.0", PORT, reuse_address=True)
        self._running = True
        
        logger.info("=" * 60)
        logger.info("OIG DIAGNOSTIC CLOUD SERVER")
        logger.info("=" * 60)
        logger.info(f"OIG Protocol: port {PORT}")
        logger.info(f"Web Dashboard: port {WEB_PORT}")
        logger.info(f"Telemetry API: port {API_PORT}")
        logger.info(f"Data directory: {self.data_dir}")
        logger.info(f"Known clients: {len(self._clients)}")
        logger.info("=" * 60)
        
        asyncio.create_task(self._periodic_cleanup())
        asyncio.create_task(self._periodic_save())
        await self._start_web_server()
        await self._start_api_server()
        
    async def stop(self):
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._api_server:
            self._api_server.close()
            await self._api_server.wait_closed()
        self._save_clients()
        logger.info("Server stopped")
        
    async def run_forever(self):
        await self.start()
        loop = asyncio.get_event_loop()
        stop_event = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
        await self.stop()
        
    async def _handle_connection(self, reader, writer):
        self._connection_counter += 1
        conn_id = self._connection_counter
        peer = writer.get_extra_info('peername')
        client_ip = peer[0] if peer else "unknown"
        client_port = peer[1] if peer else 0
        
        session_start = time.time()
        last_frame_time = session_start
        bytes_received = 0
        bytes_sent = 0
        frame_gaps = []
        
        logger.info(f"[{conn_id}] Connection from {client_ip}:{client_port}")
        self._add_activity({"type": "connect", "time": datetime.now().strftime("%H:%M:%S"),
                           "conn": conn_id, "ip": client_ip, "port": client_port})
        
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
                    
                now = time.time()
                if frames_in_connection > 0:
                    frame_gaps.append(now - last_frame_time)
                last_frame_time = now
                
                bytes_received += len(data)
                frame = data.decode('utf-8', errors='ignore')
                self._total_frames += 1
                frames_in_connection += 1
                
                parsed = self._parse_frame(frame)
                frame_device_id = parsed.get("device_id")
                table_name = parsed.get("table_name", "unknown")
                
                if frame_device_id and frame_device_id != "0000000000":
                    device_id = frame_device_id
                    self._track_client(device_id, client_ip, client_port, table_name)
                    self._save_frame(device_id, frame, parsed, client_ip, client_port, conn_id)
                    self._add_activity({"type": "frame", "time": datetime.now().strftime("%H:%M:%S"),
                                       "conn": conn_id, "ip": client_ip, "device": device_id,
                                       "table": table_name, "size": len(frame)})
                    
                logger.info(f"[{conn_id}] {table_name:15} Device={device_id or '?'} Size={len(frame)}")
                
                await asyncio.sleep(0.01)
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
            except:
                pass
            
            session_duration = time.time() - session_start
            avg_gap = sum(frame_gaps) / len(frame_gaps) if frame_gaps else 0
            
            logger.info(f"[{conn_id}] Closed. Frames={frames_in_connection} Device={device_id or '?'} "
                       f"Duration={session_duration:.1f}s Reason={close_reason}")
            
            self._add_activity({"type": "disconnect", "time": datetime.now().strftime("%H:%M:%S"),
                               "conn": conn_id, "ip": client_ip, "device": device_id or "?",
                               "frames": frames_in_connection, "duration": round(session_duration, 1),
                               "reason": close_reason})
            
            if device_id:
                self._track_session_end(device_id, bytes_received, bytes_sent)
                self._save_session(device_id, {
                    "conn_id": conn_id, "client_ip": client_ip, "client_port": client_port,
                    "start": datetime.fromtimestamp(session_start).isoformat(),
                    "duration_sec": round(session_duration, 2), "frames": frames_in_connection,
                    "bytes_rx": bytes_received, "bytes_tx": bytes_sent,
                    "avg_gap_ms": round(avg_gap * 1000, 1), "close_reason": close_reason
                })
                self._save_clients()
            
    def _parse_frame(self, frame: str) -> dict:
        result = {}
        for key, pattern in [("table_name", r'<TblName>([^<]+)</TblName>'),
                             ("result", r'<Result>([^<]+)</Result>'),
                             ("device_id", r'<ID_Device>(\d+)</ID_Device>')]:
            match = re.search(pattern, frame)
            if match:
                result[key] = match.group(1)
        return result
        
    def _track_client(self, device_id: str, client_ip: str, client_port: int, table_name: str):
        now = datetime.now().isoformat()
        if device_id not in self._clients:
            self._clients[device_id] = ClientInfo(device_id=device_id, first_seen=now, last_seen=now)
            logger.info(f"NEW CLIENT: {device_id} from {client_ip}")
        client = self._clients[device_id]
        client.last_seen = now
        client.client_ips.add(client_ip)
        client.client_ports.add(client_port)
        client.total_frames += 1
        client.tables_seen.add(table_name)
        
    def _track_session_end(self, device_id: str, bytes_rx: int, bytes_tx: int):
        if device_id in self._clients:
            client = self._clients[device_id]
            client.total_connections += 1
            client.total_bytes_rx += bytes_rx
            client.total_bytes_tx += bytes_tx
            
    def _save_frame(self, device_id: str, frame: str, parsed: dict, client_ip: str, client_port: int, conn_id: int):
        device_dir = self.data_dir / "frames" / device_id
        device_dir.mkdir(parents=True, exist_ok=True)
        daily_file = device_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        record = {"ts": datetime.now().isoformat(), "conn": conn_id, "ip": client_ip, "port": client_port,
                  "table": parsed.get("table_name", "unknown"), "size": len(frame), "raw": frame}
        with open(daily_file, 'a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            
    def _save_session(self, device_id: str, session_info: dict):
        device_dir = self.data_dir / "sessions" / device_id
        device_dir.mkdir(parents=True, exist_ok=True)
        sessions_file = device_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with open(sessions_file, 'a') as f:
            f.write(json.dumps(session_info, ensure_ascii=False) + "\n")
            
    def _generate_ack(self, parsed: dict) -> str:
        if parsed.get("result") == "IsNewSet":
            return local_oig_crc.build_frame("<Result>END</Result>")
        return local_oig_crc.build_frame("<Result>ACK</Result><ToDo>GetActual</ToDo>")
            
    async def _periodic_cleanup(self):
        while self._running:
            await asyncio.sleep(3600)
            try:
                cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
                frames_dir = self.data_dir / "frames"
                if frames_dir.exists():
                    for device_dir in frames_dir.iterdir():
                        if device_dir.is_dir():
                            for f in device_dir.glob("*.jsonl"):
                                try:
                                    if datetime.strptime(f.stem, "%Y-%m-%d") < cutoff:
                                        f.unlink()
                                except ValueError:
                                    pass
                self.db.cleanup_old_telemetry(RETENTION_DAYS)
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
                
    async def _periodic_save(self):
        while self._running:
            await asyncio.sleep(300)
            self._save_clients()
            
    # ========== TELEMETRY API (port 5720) ==========
    
    async def _start_api_server(self):
        self._api_server = await asyncio.start_server(self._handle_api, "0.0.0.0", API_PORT, reuse_address=True)
        logger.info(f"Telemetry API on http://0.0.0.0:{API_PORT}")
        
    async def _handle_api(self, reader, writer):
        peer = writer.get_extra_info('peername')
        client_ip = peer[0] if peer else "unknown"
        
        try:
            request = await asyncio.wait_for(reader.read(16384), timeout=10.0)
            request_str = request.decode('utf-8', errors='ignore')
            lines = request_str.split('\r\n')
            if not lines:
                return
            parts = lines[0].split(' ')
            if len(parts) < 2:
                return
            method, path = parts[0], parts[1]
            
            body = ""
            body_start = request_str.find('\r\n\r\n')
            if body_start > 0:
                body = request_str[body_start + 4:]
            
            if method == "POST" and path == "/api/provision":
                response = self._api_provision(body, client_ip)
            elif method == "POST" and path == "/api/telemetry":
                response = self._api_telemetry(body, client_ip)
            elif method == "GET" and path == "/api/devices":
                response = {"count": len(self.db.get_all_devices()), "devices": self.db.get_all_devices()}
            elif method == "GET" and path.startswith("/api/device/"):
                device_id = path.split("/")[-1]
                device = self.db.get_device(device_id)
                response = {"device": device, "telemetry": self.db.get_device_telemetry(device_id, 50)} if device else {"error": "not_found"}
            elif method == "GET" and path == "/api/health":
                response = {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
            else:
                response = {"error": "not_found"}
                
            response_json = json.dumps(response)
            http_response = (f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                           f"Content-Length: {len(response_json.encode())}\r\n"
                           f"Access-Control-Allow-Origin: *\r\n\r\n{response_json}")
            writer.write(http_response.encode())
            await writer.drain()
        except Exception as e:
            logger.error(f"API error: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass
                
    def _api_provision(self, body: str, client_ip: str) -> dict:
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return {"error": "invalid_json"}
            
        device_id = data.get("device_id")
        instance_hash = data.get("instance_hash", "")
        version = data.get("version", "unknown")
        
        if not device_id:
            return {"error": "missing_device_id"}
        if not re.match(r'^\d{10}$', device_id):
            return {"error": "invalid_device_id"}
            
        token = self.db.register_device(device_id, instance_hash, version, client_ip)
        self.db.store_event(device_id, "provisioned", f"ip={client_ip}, version={version}")
        self._add_activity({"type": "provision", "time": datetime.now().strftime("%H:%M:%S"),
                          "device": device_id, "ip": client_ip, "version": version})
        logger.info(f"Provisioned device {device_id} from {client_ip} (v{version})")
        return {"token": token, "expires_days": TOKEN_EXPIRY_DAYS}
        
    def _api_telemetry(self, body: str, client_ip: str) -> dict:
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return {"error": "invalid_json"}
            
        token = data.get("token")
        if not token:
            return {"error": "missing_token"}
            
        device = self.db.get_device_by_token(token)
        if not device:
            return {"error": "invalid_token"}
            
        device_id = device["device_id"]
        self.db.store_telemetry(device_id, data)
        self.db.update_device_seen(device_id, client_ip)
        logger.debug(f"Telemetry from {device_id}")
        return {"status": "ok", "device_id": device_id}
            
    # ========== WEB DASHBOARD (port 8080) ==========
    
    async def _start_web_server(self):
        self._web_server = await asyncio.start_server(self._handle_http, "0.0.0.0", WEB_PORT, reuse_address=True)
        logger.info(f"Web dashboard on http://0.0.0.0:{WEB_PORT}")
        
    async def _handle_http(self, reader, writer):
        try:
            request = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            request_line = request.decode().split('\r\n')[0]
            _, path, _ = request_line.split(' ', 2)
            
            if path == '/api/status':
                uptime = (datetime.now() - self._start_time).total_seconds()
                response = json.dumps({"uptime_str": str(timedelta(seconds=int(uptime))),
                                      "total_frames": self._total_frames, "total_clients": len(self._clients),
                                      "registered_devices": len(self.db.get_all_devices()),
                                      "connections": self._connection_counter})
                content_type = 'application/json'
            elif path == '/api/activity':
                response = json.dumps(self._activity_log[-50:][::-1])
                content_type = 'application/json'
            elif path == '/api/clients':
                response = json.dumps({k: v.to_dict() for k, v in self._clients.items()})
                content_type = 'application/json'
            elif path == '/api/registered':
                response = json.dumps({"devices": self.db.get_all_devices()})
                content_type = 'application/json'
            elif path.startswith('/api/frames/'):
                device_id = path.split('/')[-1]
                frames_file = self.data_dir / "frames" / device_id / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
                frames = []
                if frames_file.exists():
                    for line in frames_file.read_text().split('\n')[-20:]:
                        if line.strip():
                            try:
                                frames.append(json.loads(line))
                            except:
                                pass
                response = json.dumps(frames[::-1])
                content_type = 'application/json'
            else:
                response = self._dashboard_html()
                content_type = 'text/html'
                
            http_response = (f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}; charset=utf-8\r\n"
                           f"Content-Length: {len(response.encode())}\r\nAccess-Control-Allow-Origin: *\r\n\r\n{response}")
            writer.write(http_response.encode())
            await writer.drain()
        except Exception as e:
            logger.debug(f"HTTP error: {e}")
        finally:
            writer.close()
            
    def _dashboard_html(self) -> str:
        return '''<!DOCTYPE html>
<html><head><title>OIG Diagnostic</title><meta charset="utf-8">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui;background:#1a1a2e;color:#eee;padding:20px}
h1{color:#00d4ff;margin-bottom:20px}
h2{color:#888;font-size:14px;text-transform:uppercase;margin:20px 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px}
.card{background:#16213e;border-radius:8px;padding:15px}
.stat{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #2a2a4a}
.stat:last-child{border:none}
.stat-label{color:#888}
.stat-value{color:#00d4ff;font-weight:bold}
.activity{max-height:400px;overflow-y:auto}
.event{padding:8px;border-bottom:1px solid #2a2a4a;font-family:monospace;font-size:13px}
.event:hover{background:#1f2b4a}
.event-time{color:#666}
.event-connect{color:#4ade80}
.event-disconnect{color:#f87171}
.event-frame{color:#60a5fa}
.event-provision{color:#fbbf24}
.client{padding:10px;margin-bottom:10px;background:#1f2b4a;border-radius:4px;cursor:pointer}
.client:hover{background:#2a3a5a}
.client-id{font-weight:bold;color:#00d4ff}
.client-meta{color:#888;font-size:12px;margin-top:5px}
.registered{padding:8px;border-bottom:1px solid #2a2a4a}
.registered-id{color:#fbbf24;font-weight:bold}
.registered-meta{color:#666;font-size:11px}
</style></head>
<body>
<h1>OIG Diagnostic Cloud</h1>
<div class="grid">
<div class="card"><h2>Server Status</h2><div id="status">Loading...</div></div>
<div class="card"><h2>OIG Protocol Clients</h2><div id="clients">Loading...</div></div>
<div class="card"><h2>Registered Telemetry</h2><div id="registered">Loading...</div></div>
</div>
<div class="grid" style="margin-top:20px">
<div class="card"><h2>Live Activity</h2><div id="activity" class="activity">Loading...</div></div>
</div>
<script>
async function fetchStatus(){
const r=await fetch('/api/status');const d=await r.json();
document.getElementById('status').innerHTML=`
<div class="stat"><span class="stat-label">Uptime</span><span class="stat-value">${d.uptime_str}</span></div>
<div class="stat"><span class="stat-label">Frames</span><span class="stat-value">${d.total_frames}</span></div>
<div class="stat"><span class="stat-label">OIG Clients</span><span class="stat-value">${d.total_clients}</span></div>
<div class="stat"><span class="stat-label">Registered</span><span class="stat-value">${d.registered_devices}</span></div>
<div class="stat"><span class="stat-label">Connections</span><span class="stat-value">${d.connections}</span></div>
`;}
async function fetchClients(){
const r=await fetch('/api/clients');const d=await r.json();
const html=Object.values(d).map(c=>`<div class="client"><div class="client-id">${c.device_id}</div>
<div class="client-meta">${c.client_ips.join(', ')} - ${c.total_frames} frames</div></div>`).join('')||'<div style="color:#888">No clients</div>';
document.getElementById('clients').innerHTML=html;}
async function fetchRegistered(){
const r=await fetch('/api/registered');const d=await r.json();
const html=d.devices.map(x=>`<div class="registered"><div class="registered-id">${x.device_id}</div>
<div class="registered-meta">v${x.version||'?'} - ${x.last_ip||'?'} - ${x.last_seen?x.last_seen.split('T')[0]:'?'}</div></div>`).join('')||'<div style="color:#888">No devices</div>';
document.getElementById('registered').innerHTML=html;}
async function fetchActivity(){
const r=await fetch('/api/activity');const d=await r.json();
const html=d.map(e=>{
if(e.type==='connect')return`<div class="event"><span class="event-time">${e.time}</span> <span class="event-connect">CONNECT</span> [${e.conn}] ${e.ip}</div>`;
if(e.type==='disconnect')return`<div class="event"><span class="event-time">${e.time}</span> <span class="event-disconnect">DISCONNECT</span> [${e.conn}] ${e.device} ${e.frames}f ${e.duration}s</div>`;
if(e.type==='provision')return`<div class="event"><span class="event-time">${e.time}</span> <span class="event-provision">PROVISION</span> ${e.device} from ${e.ip}</div>`;
return`<div class="event"><span class="event-time">${e.time}</span> <span class="event-frame">FRAME</span> [${e.conn}] ${e.device} ${e.table}</div>`;
}).join('')||'<div style="color:#888">No activity</div>';
document.getElementById('activity').innerHTML=html;}
async function refresh(){await Promise.all([fetchStatus(),fetchClients(),fetchRegistered(),fetchActivity()]);}
refresh();setInterval(refresh,3000);
</script></body></html>'''


async def main():
    server = DiagnosticCloudServer()
    await server.run_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped")
