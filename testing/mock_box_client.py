#!/usr/bin/env python3
"""
Mock BOX Client - simuluje OIG BOX.

Přehrává reálné frames z databáze/JSON a posílá je do proxy.
Čeká na ACK responses a měří latenci.
"""
# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,logging-fstring-interpolation,broad-exception-caught,unspecified-encoding,import-outside-toplevel,unused-import,unused-argument,too-many-locals,too-many-statements,too-many-branches,too-many-instance-attributes,f-string-without-interpolation,line-too-long,too-many-nested-blocks,too-many-return-statements,no-else-return,unused-variable,no-else-continue,duplicate-code

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MOCK-BOX] [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class MockBoxClient:
    """Mock OIG BOX client."""

    def __init__(self, proxy_host: str = "localhost", proxy_port: int = 5710):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.frames_sent = 0
        self.acks_received = 0
        self.timeouts = 0
        self.latencies = []

    async def send_frames(
        self,
        frames: list[dict],
        rate_limit: float = 0.0,
        ack_timeout: float = 5.0
    ):
        """Pošle frames do proxy a čeká na ACK."""

        logger.info(f"🔌 Connecting to proxy {self.proxy_host}:{self.proxy_port}")

        try:
            reader, writer = await asyncio.open_connection(
                self.proxy_host, self.proxy_port
            )
            logger.info("✅ Connected to proxy")

            for i, frame_data in enumerate(frames, 1):
                frame = frame_data["frame"]
                table = frame_data.get("table_name", "unknown")

                # Send frame
                start_time = time.time()
                writer.write(frame.encode("utf-8"))
                await writer.drain()
                self.frames_sent += 1

                logger.info(
                    f"[{i}/{len(frames)}] Sent {table} "
                    f"({len(frame)} bytes)"
                )

                # Wait for ACK
                try:
                    response = await asyncio.wait_for(
                        reader.read(4096), timeout=ack_timeout
                    )

                    if response:
                        latency = (time.time() - start_time) * 1000  # ms
                        self.latencies.append(latency)
                        self.acks_received += 1

                        response_text = response.decode("utf-8", errors="ignore")
                        logger.debug(
                            f"   ← ACK ({latency:.1f}ms): {response_text[:50]}..."
                        )
                    else:
                        logger.error("   ✗ Empty response!")

                except asyncio.TimeoutError:
                    logger.error(
                        f"   ✗ ACK timeout after {ack_timeout}s!"
                    )
                    self.timeouts += 1

                # Rate limiting (simulace BOX behavior)
                if rate_limit > 0:
                    await asyncio.sleep(rate_limit)

            # Close connection
            writer.close()
            await writer.wait_closed()

            logger.info("✅ All frames sent")
            self.print_stats()

        except Exception as e:
            logger.error(f"❌ Connection error: {e}")
            raise

    def print_stats(self):
        """Vytiskne statistiky."""
        logger.info("")
        logger.info("=" * 60)
        logger.info("📊 STATISTICS")
        logger.info("=" * 60)
        logger.info(f"Frames sent:     {self.frames_sent}")
        logger.info(f"ACKs received:   {self.acks_received}")
        logger.info(f"Timeouts:        {self.timeouts}")

        if self.latencies:
            avg_lat = sum(self.latencies) / len(self.latencies)
            min_lat = min(self.latencies)
            max_lat = max(self.latencies)

            # Percentiles
            sorted_lat = sorted(self.latencies)
            p50 = sorted_lat[len(sorted_lat) // 2]
            p95 = sorted_lat[int(len(sorted_lat) * 0.95)]
            p99 = sorted_lat[int(len(sorted_lat) * 0.99)]

            logger.info("")
            logger.info("ACK Latency:")
            logger.info(f"  Avg: {avg_lat:.1f}ms")
            logger.info(f"  Min: {min_lat:.1f}ms")
            logger.info(f"  Max: {max_lat:.1f}ms")
            logger.info(f"  P50: {p50:.1f}ms")
            logger.info(f"  P95: {p95:.1f}ms")
            logger.info(f"  P99: {p99:.1f}ms")

        success_rate = (self.acks_received / max(1, self.frames_sent)) * 100
        logger.info("")
        logger.info(f"Success rate:    {success_rate:.1f}%")
        logger.info("=" * 60)


def load_frames_from_json(json_file: str) -> list[dict]:
    """Načte frames z JSON souboru."""
    with open(json_file, "r") as f:
        frames = json.load(f)
    logger.info(f"📦 Loaded {len(frames)} frames from {json_file}")
    return frames


async def main():
    """Spustí mock BOX client."""
    import argparse

    parser = argparse.ArgumentParser(description="Mock OIG BOX Client")
    parser.add_argument(
        "--data",
        required=True,
        help="JSON file with frames (e.g., test_data/box_frames_100.json)"
    )
    parser.add_argument(
        "--proxy-host",
        default="localhost",
        help="Proxy host (default: localhost)"
    )
    parser.add_argument(
        "--proxy-port",
        type=int,
        default=5710,
        help="Proxy port (default: 5710)"
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=0.0,
        help="Delay between frames in seconds (default: 0 = as fast as possible)"
    )
    parser.add_argument(
        "--ack-timeout",
        type=float,
        default=5.0,
        help="ACK timeout in seconds (default: 5.0)"
    )

    args = parser.parse_args()

    # Load frames
    frames = load_frames_from_json(args.data)

    # Create client
    client = MockBoxClient(
        proxy_host=args.proxy_host,
        proxy_port=args.proxy_port
    )

    # Send frames
    await client.send_frames(
        frames=frames,
        rate_limit=args.rate_limit,
        ack_timeout=args.ack_timeout
    )


if __name__ == "__main__":
    asyncio.run(main())
