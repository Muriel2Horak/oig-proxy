#!/usr/bin/env python3
"""
Replay BOX frames from local payloads.db to proxy.

Načte framy z DB a pošle je na proxy jako by přicházely z BOXu.
Respektuje timestampy pro realistickou simulaci.
"""

import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


class DBReplayClient:
    """Replay frames from database to proxy."""

    def __init__(
        self,
        db_path: str,
        proxy_host: str = "localhost",
        proxy_port: int = 5710,
        device_id: str = None,
        speed_factor: float = 1.0
    ):
        self.db_path = db_path
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.device_id = device_id
        self.speed_factor = speed_factor

        self.frames_sent = 0
        self.acks_received = 0
        self.errors = 0

    def load_frames(self, limit: int = None, table_filter: str = None) -> list[dict]:
        """Load frames from database."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        query = """
            SELECT ts, device_id, table_name, raw, direction
            FROM frames
            WHERE direction IN ('box_to_cloud', 'box_to_proxy')
        """
        params = []

        if self.device_id:
            query += " AND device_id = ?"
            params.append(self.device_id)

        if table_filter:
            query += " AND table_name = ?"
            params.append(table_filter)

        query += " ORDER BY ts ASC"

        if limit:
            query += f" LIMIT {limit}"

        cursor = conn.execute(query, params)
        frames = []

        for row in cursor:
            frames.append({
                "ts": row["ts"],
                "device_id": row["device_id"],
                "table_name": row["table_name"],
                "raw": row["raw"],
            })

        conn.close()
        print(f"📦 Loaded {len(frames)} frames from {self.db_path}")
        return frames

    def parse_timestamp(self, ts_str: str) -> datetime:
        """Parse timestamp string to datetime."""
        # Handle various formats
        for fmt in [
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
        ]:
            try:
                return datetime.strptime(ts_str, fmt)
            except ValueError:
                continue
        # Fallback - strip timezone if present
        if "+" in ts_str:
            ts_str = ts_str.split("+")[0]
        return datetime.fromisoformat(ts_str)

    async def replay(
        self,
        frames: list[dict],
        realtime: bool = True,
        max_delay: float = 5.0
    ):
        """Replay frames to proxy."""
        print(f"\n🔌 Connecting to proxy {self.proxy_host}:{self.proxy_port}")

        try:
            reader, writer = await asyncio.open_connection(
                self.proxy_host, self.proxy_port
            )
            print(f"✅ Connected!")
        except Exception as e:
            print(f"❌ Connection failed: {e}")
            return

        prev_ts = None
        start_time = datetime.now()

        print(f"\n🎬 Starting replay of {len(frames)} frames...")
        print(f"   Speed factor: {self.speed_factor}x")
        print(f"   Max delay: {max_delay}s")
        print()

        try:
            for i, frame in enumerate(frames, 1):
                # Calculate delay based on timestamps
                if realtime and prev_ts:
                    curr_ts = self.parse_timestamp(frame["ts"])
                    prev_dt = self.parse_timestamp(prev_ts)

                    # Make both naive for comparison
                    if curr_ts.tzinfo:
                        curr_ts = curr_ts.replace(tzinfo=None)
                    if prev_dt.tzinfo:
                        prev_dt = prev_dt.replace(tzinfo=None)

                    delay = (curr_ts - prev_dt).total_seconds()
                    delay = delay / self.speed_factor  # Speed up/slow down

                    if delay > 0 and delay <= max_delay:
                        await asyncio.sleep(delay)

                prev_ts = frame["ts"]

                # Send frame
                raw = frame["raw"]
                if not raw.endswith("\r\n"):
                    raw += "\r\n"

                writer.write(raw.encode("utf-8"))
                await writer.drain()
                self.frames_sent += 1

                # Visual indicator
                table = frame["table_name"] or "unknown"
                progress = f"[{i}/{len(frames)}]"
                elapsed = (datetime.now() - start_time).total_seconds()
                rate = self.frames_sent / elapsed if elapsed > 0 else 0

                print(
                    f"\r{progress} Sent: {table:12} | "
                    f"Total: {self.frames_sent} | "
                    f"Rate: {rate:.1f}/s | "
                    f"ACKs: {self.acks_received}",
                    end="", flush=True
                )

                # Try to read ACK (non-blocking)
                try:
                    response = await asyncio.wait_for(
                        reader.read(4096), timeout=0.1
                    )
                    if response:
                        self.acks_received += 1
                except asyncio.TimeoutError:
                    pass  # No ACK yet, continue

        except Exception as e:
            print(f"\n❌ Error: {e}")
            self.errors += 1
        finally:
            # Drain remaining ACKs
            try:
                while True:
                    response = await asyncio.wait_for(
                        reader.read(4096), timeout=1.0
                    )
                    if response:
                        self.acks_received += 1
                    else:
                        break
            except asyncio.TimeoutError:
                pass

            writer.close()
            await writer.wait_closed()

        self.print_summary(start_time)

    def print_summary(self, start_time: datetime):
        """Print replay summary."""
        elapsed = (datetime.now() - start_time).total_seconds()
        rate = self.frames_sent / elapsed if elapsed > 0 else 0

        print("\n")
        print("=" * 60)
        print("📊 REPLAY SUMMARY")
        print("=" * 60)
        print(f"  Frames sent:   {self.frames_sent}")
        print(f"  ACKs received: {self.acks_received}")
        print(f"  Errors:        {self.errors}")
        print(f"  Duration:      {elapsed:.1f}s")
        print(f"  Avg rate:      {rate:.1f} frames/s")

        if self.frames_sent > 0:
            ack_rate = (self.acks_received / self.frames_sent) * 100
            print(f"  ACK rate:      {ack_rate:.1f}%")
        print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(
        description="Replay BOX frames from database to proxy"
    )
    parser.add_argument(
        "--db",
        default="analysis/payloads.db",
        help="Path to payloads database"
    )
    parser.add_argument(
        "--proxy-host",
        default="10.0.0.143",
        help="Proxy host (default: 10.0.0.143 - HA server)"
    )
    parser.add_argument(
        "--proxy-port",
        type=int,
        default=5710,
        help="Proxy port (default: 5710)"
    )
    parser.add_argument(
        "--device-id",
        default="2206237016",
        help="Filter by device ID"
    )
    parser.add_argument(
        "--table",
        help="Filter by table name (e.g., tbl_actual)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of frames"
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=10.0,
        help="Speed factor (1.0 = realtime, 10.0 = 10x faster)"
    )
    parser.add_argument(
        "--max-delay",
        type=float,
        default=2.0,
        help="Maximum delay between frames in seconds"
    )
    parser.add_argument(
        "--no-delay",
        action="store_true",
        help="Send frames as fast as possible (no timing)"
    )

    args = parser.parse_args()

    # Resolve DB path
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = Path(__file__).parent.parent / args.db

    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        sys.exit(1)

    client = DBReplayClient(
        db_path=str(db_path),
        proxy_host=args.proxy_host,
        proxy_port=args.proxy_port,
        device_id=args.device_id,
        speed_factor=args.speed
    )

    frames = client.load_frames(
        limit=args.limit,
        table_filter=args.table
    )

    if not frames:
        print("❌ No frames found")
        sys.exit(1)

    await client.replay(
        frames=frames,
        realtime=not args.no_delay,
        max_delay=args.max_delay
    )


if __name__ == "__main__":
    asyncio.run(main())
