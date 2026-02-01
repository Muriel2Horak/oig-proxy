#!/usr/bin/env python3
# pylint: disable=broad-exception-caught,too-many-instance-attributes,too-many-arguments
# pylint: disable=too-many-positional-arguments,logging-fstring-interpolation
# pylint: disable=missing-function-docstring,unused-import
"""
Mock OIG Cloud Server - Capture & Analyze.

Server simulující oigservis.cz:5710 pro diagnostiku.

Funkce:
1. Přijímá spojení od proxy/BOX
2. Loguje všechny přijaté framy
3. Odpovídá validními ACK
4. Ukládá data pro analýzu
5. Zobrazuje real-time statistiky

Použití na NAS:
    python mock_cloud_capture.py --port 5710 --output /path/to/captures

Pak na routeru přesměrovat oigservis.cz (185.25.185.30) na NAS IP.

Nebo v proxy config nastavit:
    CLOUD_HOST: "nas_ip"
    CLOUD_PORT: 5710
"""

import argparse
import asyncio
import json
import logging
import os  # noqa: F401
import re
import signal
import sys  # noqa: F401
import time  # noqa: F401
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [MockCloud] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("mock_cloud")


@dataclass
class FrameCapture:
    """Zachycený frame."""
    timestamp: str
    connection_id: int
    client_ip: str
    client_port: int
    direction: str  # "received" | "sent"
    frame_type: str  # table name or result
    frame_size: int
    frame_data: str
    parse_result: dict = field(default_factory=dict)


@dataclass
class ConnectionStats:
    """Statistiky jednoho spojení."""
    connection_id: int
    client_ip: str
    client_port: int
    start_time: str
    end_time: Optional[str] = None
    frames_received: int = 0
    frames_sent: int = 0
    bytes_received: int = 0
    bytes_sent: int = 0
    tables_seen: list = field(default_factory=list)
    device_ids: set = field(default_factory=set)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['device_ids'] = list(self.device_ids)
        return d


class MockCloudServer:
    """Mock OIG Cloud server pro diagnostiku."""

    # ACK odpovědi pro různé tabulky
    ACK_TEMPLATES = {
        "default": (
            '<Frame><Result>ACK</Result><ToDo>GetAll</ToDo>'
            '<Time>{time}</Time><CRC>12345</CRC></Frame>\r\n'
        ),
        "END": (
            '<Frame><Result>ACK</Result><ToDo>GetAll</ToDo>'
            '<Time>{time}</Time><CRC>99999</CRC></Frame>\r\n'
        ),
    }

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5710,
        output_dir: Optional[str] = None,
        response_delay_ms: int = 10,
        verbose: bool = False
    ):
        self.host = host
        self.port = port
        self.output_dir = Path(output_dir) if output_dir else Path("/tmp/mock_cloud_capture")
        self.response_delay_ms = response_delay_ms
        self.verbose = verbose

        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._server: Optional[asyncio.Server] = None
        self._running = False
        self._connection_counter = 0
        self._background_tasks: set = set()  # Prevent task GC

        # Statistics
        self._total_frames = 0
        self._total_connections = 0
        self._connections: dict[int, ConnectionStats] = {}
        self._captures: list[FrameCapture] = []
        self._start_time = datetime.now()

        # Periodic save
        self._save_interval = 60  # seconds

    async def start(self):
        """Start mock server."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port,
            reuse_address=True
        )
        self._running = True

        self._print_banner()

        # Start periodic save task
        task = asyncio.create_task(self._periodic_save())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _print_banner(self):
        """Print server banner."""
        print("""
╔══════════════════════════════════════════════════════════════╗
║          MOCK OIG CLOUD CAPTURE SERVER                       ║
╠══════════════════════════════════════════════════════════════╣
║  Zachytává a loguje veškerou komunikaci od proxy/BOX         ║
╚══════════════════════════════════════════════════════════════╝
""")
        print(f"  Listening:    {self.host}:{self.port}")
        print(f"  Output dir:   {self.output_dir}")
        print(f"  Response delay: {self.response_delay_ms}ms")
        print(f"  Started:      {self._start_time.isoformat()}")
        print()
        print("  Press Ctrl+C to stop and save captures")
        print()
        print("=" * 60)
        print("WAITING FOR CONNECTIONS...")
        print("=" * 60)
        print()

    async def stop(self):
        """Stop server and save captures."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Final save
        await self._save_captures()
        self._print_summary()

    async def run_forever(self):
        """Run server until interrupted."""
        await self.start()

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        # Wait for server
        async with self._server:
            await self._server.serve_forever()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        """Handle incoming connection."""
        self._connection_counter += 1
        self._total_connections += 1
        conn_id = self._connection_counter

        peer = writer.get_extra_info('peername')
        client_ip = peer[0] if peer else "unknown"
        client_port = peer[1] if peer else 0

        # Create connection stats
        stats = ConnectionStats(
            connection_id=conn_id,
            client_ip=client_ip,
            client_port=client_port,
            start_time=datetime.now().isoformat()
        )
        self._connections[conn_id] = stats

        logger.info(f"🔌 Connection #{conn_id} from {client_ip}:{client_port}")

        try:
            while self._running:
                # Read data
                try:
                    data = await asyncio.wait_for(reader.read(8192), timeout=60.0)
                    if not data:
                        break
                except asyncio.TimeoutError:
                    logger.debug(f"#{conn_id}: Read timeout, closing")
                    break

                frame = data.decode('utf-8', errors='ignore')
                self._total_frames += 1
                stats.frames_received += 1
                stats.bytes_received += len(data)

                # Parse frame
                parsed = self._parse_frame(frame)

                # Log and capture
                self._log_frame(conn_id, client_ip, client_port, "received", frame, parsed)

                # Update stats
                if parsed.get("table_name"):
                    if parsed["table_name"] not in stats.tables_seen:
                        stats.tables_seen.append(parsed["table_name"])
                if parsed.get("device_id"):
                    stats.device_ids.add(parsed["device_id"])

                # Generate and send response
                await asyncio.sleep(self.response_delay_ms / 1000.0)

                response = self._generate_ack(parsed)
                writer.write(response.encode('utf-8'))
                await writer.drain()

                stats.frames_sent += 1
                stats.bytes_sent += len(response)

                # Log response
                self._log_frame(conn_id, client_ip, client_port, "sent", response, {"type": "ACK"})

        except Exception as e:
            logger.error(f"#{conn_id}: Error: {e}")
        finally:
            stats.end_time = datetime.now().isoformat()

            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

            logger.info(
                f"🔌 Connection #{conn_id} closed. "
                f"Received: {stats.frames_received} frames, {stats.bytes_received} bytes"
            )

    def _parse_frame(self, frame: str) -> dict:
        """Parse OIG frame and extract key fields."""
        result = {}

        # Table name
        tbl_match = re.search(r'<TblName>([^<]+)</TblName>', frame)
        if tbl_match:
            result["table_name"] = tbl_match.group(1)

        # Result type (ACK, END, IsNewSet, etc.)
        result_match = re.search(r'<Result>([^<]+)</Result>', frame)
        if result_match:
            result["result"] = result_match.group(1)

        # Device ID
        device_match = re.search(r'<ID_Device>(\d+)</ID_Device>', frame)
        if device_match:
            result["device_id"] = device_match.group(1)

        # ID_Set
        idset_match = re.search(r'<ID_Set>(\d+)</ID_Set>', frame)
        if idset_match:
            result["id_set"] = idset_match.group(1)

        # Timestamp
        dt_match = re.search(r'<DT>([^<]+)</DT>', frame)
        if dt_match:
            result["timestamp"] = dt_match.group(1)

        # Reason
        reason_match = re.search(r'<Reason>([^<]+)</Reason>', frame)
        if reason_match:
            result["reason"] = reason_match.group(1)

        # CRC
        crc_match = re.search(r'<CRC>(\d+)</CRC>', frame)
        if crc_match:
            result["crc"] = crc_match.group(1)

        return result

    def _log_frame(
        self,
        conn_id: int,
        client_ip: str,
        client_port: int,
        direction: str,
        frame: str,
        parsed: dict
    ):
        """Log and capture frame."""
        ts = datetime.now()

        frame_type = parsed.get("table_name") or parsed.get("result") or "unknown"

        # Create capture
        capture = FrameCapture(
            timestamp=ts.isoformat(),
            connection_id=conn_id,
            client_ip=client_ip,
            client_port=client_port,
            direction=direction,
            frame_type=frame_type,
            frame_size=len(frame),
            frame_data=frame,
            parse_result=parsed
        )
        self._captures.append(capture)

        # Console log
        icon = "📥" if direction == "received" else "📤"
        ts_str = ts.strftime("%H:%M:%S.%f")[:-3]

        if direction == "received":
            device = parsed.get("device_id", "?")
            reason = parsed.get("reason", "")
            logger.info(
                f"{icon} #{conn_id} {ts_str} {frame_type:15} "
                f"Device={device} Reason={reason} Size={len(frame)}"
            )

            if self.verbose:
                print(f"    Data: {frame[:200]}...")
        else:
            if self.verbose:
                logger.debug(f"{icon} #{conn_id} Sent ACK")

    def _generate_ack(self, parsed: dict) -> str:
        """Generate ACK response for frame."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if parsed.get("result") == "END":
            template = self.ACK_TEMPLATES["END"]
        else:
            template = self.ACK_TEMPLATES["default"]

        return template.format(time=now)

    async def _periodic_save(self):
        """Periodically save captures."""
        while self._running:
            await asyncio.sleep(self._save_interval)
            await self._save_captures()

    async def _save_captures(self):
        """Save all captures to files."""
        if not self._captures:
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Save captures JSON
        captures_file = self.output_dir / f"captures_{ts}.json"
        captures_data = [asdict(c) for c in self._captures]
        await asyncio.to_thread(
            lambda: captures_file.write_text(
                json.dumps(captures_data, indent=2, ensure_ascii=False), encoding='utf-8'
            )
        )

        # Save connection stats
        stats_file = self.output_dir / f"connections_{ts}.json"
        stats_data = {
            "server_start": self._start_time.isoformat(),
            "save_time": datetime.now().isoformat(),
            "total_connections": self._total_connections,
            "total_frames": self._total_frames,
            "connections": [c.to_dict() for c in self._connections.values()]
        }
        await asyncio.to_thread(
            lambda: stats_file.write_text(
                json.dumps(stats_data, indent=2, ensure_ascii=False), encoding='utf-8'
            )
        )

        logger.info(f"💾 Saved {len(self._captures)} captures to {captures_file}")

        # Save individual frames as XML for easy viewing
        frames_dir = self.output_dir / f"frames_{ts}"
        frames_dir.mkdir(exist_ok=True)

        for i, capture in enumerate(self._captures):
            if capture.direction == "received":
                frame_file = frames_dir / f"{i:04d}_{capture.frame_type}.xml"
                content = (
                    f"<!-- Connection: {capture.connection_id} -->\n"
                    f"<!-- Timestamp: {capture.timestamp} -->\n"
                    f"<!-- Client: {capture.client_ip}:{capture.client_port} -->\n"
                    + capture.frame_data
                )
                await asyncio.to_thread(
                    lambda c=content, f=frame_file: f.write_text(c, encoding='utf-8')
                )

    def _print_summary(self):
        """Print capture summary."""
        print()
        print("=" * 60)
        print("CAPTURE SUMMARY")
        print("=" * 60)

        duration = datetime.now() - self._start_time

        print(f"\n  Duration:         {duration}")
        print(f"  Total connections: {self._total_connections}")
        print(f"  Total frames:      {self._total_frames}")

        # Aggregate stats
        all_devices = set()
        all_tables = set()
        total_bytes = 0

        for stats in self._connections.values():
            all_devices.update(stats.device_ids)
            all_tables.update(stats.tables_seen)
            total_bytes += stats.bytes_received

        print(f"  Total data:        {total_bytes / 1024:.1f} KB")
        print(f"  Unique devices:    {len(all_devices)}")
        print(f"  Tables seen:       {', '.join(sorted(all_tables))}")

        print(f"\n  Output dir:        {self.output_dir}")
        print()


async def main():
    parser = argparse.ArgumentParser(
        description="Mock OIG Cloud Server - Capture & Analyze"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=5710,
        help="Port to listen on (default: 5710)"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--output", "-o",
        default="/tmp/mock_cloud_capture",
        help="Output directory for captures"
    )
    parser.add_argument(
        "--delay", "-d",
        type=int,
        default=10,
        help="Response delay in ms (default: 10)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    server = MockCloudServer(
        host=args.host,
        port=args.port,
        output_dir=args.output,
        response_delay_ms=args.delay,
        verbose=args.verbose
    )

    try:
        await server.run_forever()
    except asyncio.CancelledError:
        await server.stop()
        raise  # Re-raise CancelledError after cleanup
    finally:
        await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Server stopped by user")
