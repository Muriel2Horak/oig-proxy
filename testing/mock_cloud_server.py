#!/usr/bin/env python3
"""
Mock OIG Cloud Server - pro testování proxy.

Simuluje chování oigservis.cz:5710:
- Přijímá TCP spojení
- Parsuje XML frames od BOXu (přes proxy)
- Posílá ACK/END responses (fixní ACK/END s CRC)
- Loguje všechny frames pro validaci
"""
# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,logging-fstring-interpolation,broad-exception-caught,unspecified-encoding,import-outside-toplevel,unused-import,unused-argument,too-many-locals,too-many-statements,too-many-branches,too-many-instance-attributes,f-string-without-interpolation,line-too-long,too-many-nested-blocks,too-many-return-statements,no-else-return,unused-variable,no-else-continue,duplicate-code

import argparse
import asyncio
import datetime
import json
import logging
import os
import re
import sys
from typing import Optional, Dict, Any
from aiohttp import web, WSMsgType

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [MOCK-CLOUD] [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class MockCloudServer:
    """Mock OIG cloud server."""

    # Learned ACK patterns z analýzy
    ACK_PATTERNS = {
        "tbl_actual": '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
        "tbl_dc_in": '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
        "tbl_ac_in": '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
        "tbl_ac_out": '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
        "tbl_batt": '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
        "tbl_boiler": '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
        "tbl_box": '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
        "tbl_events": '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
        "tbl_batt_prms": '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
        "tbl_invertor_prms": '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
        "tbl_box_prms": '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
        # IsNewSet polling
        "IsNewSet": '<Frame><Result>END</Result><CRC>34500</CRC></Frame>',
        "IsNewWeather": '<Frame><Result>END</Result><CRC>34500</CRC></Frame>',
        "IsNewFW": '<Frame><Result>END</Result><CRC>34500</CRC></Frame>',
        # Default
        "default": '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>',
    }

    def __init__(self, host: str = "0.0.0.0", port: int = 5710):
        self.host = host
        self.port = port
        self.connection_count = 0
        self.frames_received = []
        self.running = True
        self.pending_setting: Optional[Dict[str, Any]] = None

    async def handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        """Zpracuje připojení od proxy (simuluje BOX komunikaci)."""
        self.connection_count += 1
        conn_id = self.connection_count
        addr = writer.get_extra_info('peername')
        logger.info(f"[#{conn_id}] New connection from {addr}")

        try:
            while self.running:
                # Read data from proxy
                data = await asyncio.wait_for(reader.read(4096), timeout=120.0)
                if not data:
                    break

                text = data.decode("utf-8", errors="ignore")
                logger.debug(f"[#{conn_id}] Received: {text[:100]}...")

                # Parse table name
                table_name = self._parse_table_name(text)

                # Store frame
                self.frames_received.append({
                    "conn_id": conn_id,
                    "timestamp": datetime.datetime.now().isoformat(),
                    "table_name": table_name,
                    "frame": text
                })

                # Generate ACK response
                ack = self._generate_ack(table_name)

                # Small delay to simulate processing (8-15ms dle analýzy)
                await asyncio.sleep(0.01)

                # Send ACK
                writer.write(ack.encode("utf-8"))
                await writer.drain()

                logger.info(
                    f"[#{conn_id}] {table_name} → ACK "
                    f"(total: {len(self.frames_received)})"
                )

        except asyncio.TimeoutError:
            logger.warning(f"[#{conn_id}] Timeout")
        except Exception as e:
            logger.error(f"[#{conn_id}] Error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()
            logger.info(f"[#{conn_id}] Connection closed")

    def _parse_table_name(self, text: str) -> str:
        """Extrahuje název tabulky z frame."""
        # Hledáme <TblName>...</TblName>
        match = re.search(r'<TblName>([^<]+)</TblName>', text)
        if match:
            return match.group(1)

        # Hledáme <Result>IsNew*</Result> (box polling)
        match = re.search(r'<Result>(IsNew[^<]+)</Result>', text)
        if match:
            return match.group(1)

        # Hledáme <ToDo>IsNewSet</ToDo> apod.
        match = re.search(r'<ToDo>(IsNew[^<]+)</ToDo>', text)
        if match:
            return match.group(1)

        return "unknown"

    def _generate_ack(self, table_name: str) -> str:
        """Vygeneruje ACK response podle tabulky."""
        return self.ACK_PATTERNS.get(table_name, self.ACK_PATTERNS["default"])

    async def start(self):
        """Spustí mock cloud server."""
        tcp_server = await asyncio.start_server(
            self.handle_connection, self.host, self.port
        )
        
        app = web.Application()
        app.router.add_post('/api/queue-setting', self.handle_queue_setting)
        app.router.add_get('/api/pending', self.handle_get_pending)
        
        http_port = self.port + 1
        runner = web.AppRunner(app)
        await runner.setup()
        http_site = web.TCPSite(runner, self.host, http_port)
        await http_site.start()

        tcp_addr = tcp_server.sockets[0].getsockname()
        logger.info(f"🟢 Mock Cloud Server listening on {tcp_addr}")
        logger.info(f"🔗 HTTP API available on http://{self.host}:{http_port}")
        logger.info("   Ready to receive frames from proxy")

        async with tcp_server:
            await tcp_server.serve_forever()

    def get_stats(self) -> dict:
        """Vrátí statistiky."""
        tables = {}
        for frame in self.frames_received:
            table = frame["table_name"]
            tables[table] = tables.get(table, 0) + 1

        return {
            "total_frames": len(self.frames_received),
            "connections": self.connection_count,
            "tables": tables,
            "frames": self.frames_received
        }

    def save_frames(self, filename: str = "mock_cloud_frames.json"):
        """Uloží přijaté frames pro validaci."""
        stats = self.get_stats()
        with open(filename, "w") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"📝 Frames saved to {filename}")

    def queue_setting(self, tbl_name: str, tbl_item: str, new_value: str) -> dict:
        if not all([tbl_name, tbl_item, new_value]):
            return {
                "status": "error",
                "message": "Missing required fields: tbl_name, tbl_item, new_value"
            }
        
        self.pending_setting = {
            "tbl_name": tbl_name,
            "tbl_item": tbl_item,
            "new_value": new_value,
            "timestamp": datetime.datetime.now().isoformat()
        }
        
        logger.info(f"API: Queued setting {tbl_name}/{tbl_item}={new_value}")
        
        return {
            "status": "queued",
            "pending": self.pending_setting
        }

    def get_pending_setting(self) -> dict:
        if self.pending_setting:
            return {
                "status": "has_pending",
                "pending_setting": self.pending_setting
            }
        else:
            return {
                "status": "no_pending",
                "pending_setting": None
            }

    async def handle_queue_setting(self, request: web.Request) -> web.Response:
        try:
            if not request.content_type or 'application/json' not in request.content_type:
                return web.json_response(
                    {"status": "error", "message": "Content-Type must be application/json"},
                    status=400
                )
            
            data = await request.json()
            
            required_fields = ['tbl_name', 'tbl_item', 'new_value']
            missing_fields = [field for field in required_fields if field not in data]
            
            if missing_fields:
                return web.json_response(
                    {
                        "status": "error",
                        "message": f"Missing required fields: {', '.join(missing_fields)}"
                    },
                    status=400
                )
            
            result = self.queue_setting(
                tbl_name=data['tbl_name'],
                tbl_item=data['tbl_item'],
                new_value=data['new_value']
            )
            
            return web.json_response(result)
            
        except json.JSONDecodeError:
            return web.json_response(
                {"status": "error", "message": "Invalid JSON"},
                status=400
            )
        except Exception as e:
            logger.error(f"API Error in /api/queue-setting: {e}")
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500
            )

    async def handle_get_pending(self, request: web.Request) -> web.Response:
        try:
            result = self.get_pending_setting()
            return web.json_response(result)
        except Exception as e:
            logger.error(f"API Error in /api/pending: {e}")
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500
            )


async def main():
    """Spustí mock cloud server."""
    import signal

    parser = argparse.ArgumentParser(description="Mock OIG Cloud Server")
    parser.add_argument(
        "--host",
        default=os.getenv("MOCK_CLOUD_HOST", "0.0.0.0"),
        help="Listen host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MOCK_CLOUD_PORT", "5710")),
        help="Listen port (default: 5710)",
    )
    args = parser.parse_args()

    server = MockCloudServer(host=args.host, port=args.port)

    # Graceful shutdown
    def signal_handler(sig, frame):
        logger.info("🛑 Shutting down...")
        server.running = False
        server.save_frames()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted")
        server.save_frames()


if __name__ == "__main__":
    asyncio.run(main())
