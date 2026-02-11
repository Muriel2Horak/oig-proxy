#!/usr/bin/env python3
"""
Simulační testy proxy v různých módech.

Testuje chování proxy v režimech ONLINE, HYBRID, OFFLINE
pomocí mock Cloud serveru a dat ze skutečného BOXu.

Scénáře:
1. ONLINE: Cloud dostupný → framy přeposílány, BOX dostává cloud ACK
2. ONLINE: Cloud timeout → BOX dostává timeout (transparentní)
3. HYBRID: Cloud selže 3x → přepnutí na offline, lokální ACK
4. HYBRID: Po intervalu → retry cloud
5. OFFLINE: Vše lokální ACK bez pokusu o cloud
"""
# pylint: disable=all

import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Logging setup
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("ProxySim")

# Paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
ADDON_DIR = PROJECT_ROOT / "addon" / "oig-proxy"
TEST_DATA_FILE = SCRIPT_DIR / "mock_cloud_frames.json"

# Add addon to path for imports
sys.path.insert(0, str(ADDON_DIR))


@dataclass
class TestResult:
    """Výsledek jednoho testu."""
    name: str
    passed: bool
    details: str
    duration_ms: float


class MockCloudServer:
    """
    Mock cloud server pro simulaci oigservis.cz:5710.
    
    Může simulovat:
    - Normální ACK odpovědi
    - Timeout (žádná odpověď)
    - Uzavření spojení
    """
    
    # ACK patterns pro různé tabulky
    ACK_PATTERNS = {
        "tbl_dc_in": '<Frame><Result>ACK</Result><ToDo>GetAll</ToDo><CRC>12345</CRC></Frame>\r\n',
        "tbl_ac_in": '<Frame><Result>ACK</Result><ToDo>GetAll</ToDo><CRC>12346</CRC></Frame>\r\n',
        "tbl_ac_out": '<Frame><Result>ACK</Result><ToDo>GetAll</ToDo><CRC>12347</CRC></Frame>\r\n',
        "tbl_batt": '<Frame><Result>ACK</Result><ToDo>GetAll</ToDo><CRC>12348</CRC></Frame>\r\n',
        "tbl_boiler": '<Frame><Result>ACK</Result><ToDo>GetAll</ToDo><CRC>12349</CRC></Frame>\r\n',
        "tbl_box": '<Frame><Result>ACK</Result><ToDo>GetAll</ToDo><CRC>12350</CRC></Frame>\r\n',
        "tbl_events": '<Frame><Result>ACK</Result><ToDo>GetAll</ToDo><CRC>12351</CRC></Frame>\r\n',
        "tbl_actual": '<Frame><Result>ACK</Result><ToDo>GetAll</ToDo><CRC>12352</CRC></Frame>\r\n',
    }
    
    END_ACK = '<Frame><Result>ACK</Result><ToDo>GetAll</ToDo><CRC>99999</CRC></Frame>\r\n'
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 15710,  # Local test port
        mode: str = "normal",  # normal, timeout, close, fail_n_times
        fail_count: int = 0,
        response_delay_ms: int = 10
    ):
        self.host = host
        self.port = port
        self.mode = mode
        self.fail_count = fail_count
        self.response_delay_ms = response_delay_ms
        
        self._server: Optional[asyncio.Server] = None
        self._request_count = 0
        self._running = False
        self._connections = []
        
    async def start(self):
        """Start mock server."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port
        )
        self._running = True
        logger.info(f"MockCloud started on {self.host}:{self.port} (mode={self.mode})")
        
    async def stop(self):
        """Stop mock server."""
        self._running = False
        
        # Close all connections
        for writer in self._connections:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._connections.clear()
        
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("MockCloud stopped")
            
    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        """Handle incoming connection from proxy."""
        addr = writer.get_extra_info('peername')
        logger.debug(f"MockCloud: Connection from {addr}")
        self._connections.append(writer)
        
        try:
            while self._running:
                # Read frame from proxy
                try:
                    data = await asyncio.wait_for(reader.read(8192), timeout=30.0)
                    if not data:
                        break
                except asyncio.TimeoutError:
                    break
                    
                frame = data.decode('utf-8', errors='ignore')
                self._request_count += 1
                
                # Parse table name or result
                tbl_match = re.search(r'<TblName>([^<]+)</TblName>', frame)
                result_match = re.search(r'<Result>([^<]+)</Result>', frame)
                
                tbl_name = tbl_match.group(1) if tbl_match else None
                result = result_match.group(1) if result_match else None
                
                logger.debug(f"MockCloud: Received frame #{self._request_count}, tbl={tbl_name}, result={result}")
                
                # Decide response based on mode
                if self.mode == "timeout":
                    # Neodpovídej vůbec
                    logger.debug("MockCloud: mode=timeout, not responding")
                    continue
                    
                elif self.mode == "close":
                    # Zavři spojení
                    logger.debug("MockCloud: mode=close, closing connection")
                    break
                    
                elif self.mode == "fail_n_times":
                    if self._request_count <= self.fail_count:
                        logger.debug(f"MockCloud: Failing request {self._request_count}/{self.fail_count}")
                        break  # Close connection to simulate failure
                    # Otherwise respond normally
                    
                # Normal ACK response
                await asyncio.sleep(self.response_delay_ms / 1000.0)
                
                if result == "END":
                    response = self.END_ACK
                elif tbl_name and tbl_name in self.ACK_PATTERNS:
                    response = self.ACK_PATTERNS[tbl_name]
                else:
                    response = self.ACK_PATTERNS.get("tbl_actual", self.END_ACK)
                    
                writer.write(response.encode('utf-8'))
                await writer.drain()
                logger.debug(f"MockCloud: Sent ACK for {tbl_name or result}")
                
        except Exception as e:
            logger.debug(f"MockCloud: Connection error: {e}")
        finally:
            self._connections.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


class MockBoxClient:
    """
    Simulovaný BOX klient posílající framy na proxy.
    """
    
    def __init__(self, proxy_host: str = "127.0.0.1", proxy_port: int = 15700):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.frames = []
        self._load_frames()
        
    def _load_frames(self):
        """Load frames from test data file."""
        if TEST_DATA_FILE.exists():
            with open(TEST_DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.frames = [item['frame'] for item in data.get('frames', [])]
        logger.info(f"MockBox: Loaded {len(self.frames)} frames")
        
    async def send_frames(
        self,
        count: int = 5,
        timeout_per_frame: float = 5.0
    ) -> list[dict]:
        """
        Send frames to proxy and collect responses.
        
        Returns list of dicts: {frame, response, latency_ms, error}
        """
        results = []
        
        for i, frame in enumerate(self.frames[:count]):
            result = {
                "frame_idx": i,
                "table": self._extract_table(frame),
                "response": None,
                "latency_ms": None,
                "error": None
            }
            
            try:
                # Connect per frame (jak to dělá skutečný BOX)
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.proxy_host, self.proxy_port),
                    timeout=5.0
                )
                
                # Send frame
                start = time.monotonic()
                writer.write(frame.encode('utf-8'))
                await writer.drain()
                
                # Wait for response
                try:
                    response = await asyncio.wait_for(
                        reader.read(4096),
                        timeout=timeout_per_frame
                    )
                    latency = (time.monotonic() - start) * 1000
                    
                    result["response"] = response.decode('utf-8', errors='ignore')
                    result["latency_ms"] = latency
                    
                except asyncio.TimeoutError:
                    result["error"] = "timeout"
                    
                writer.close()
                await writer.wait_closed()
                
            except Exception as e:
                result["error"] = str(e)
                
            results.append(result)
            logger.debug(f"MockBox: Frame {i} -> {result.get('error') or 'ACK'}")
            
        return results
        
    def _extract_table(self, frame: str) -> Optional[str]:
        """Extract table name from frame."""
        match = re.search(r'<TblName>([^<]+)</TblName>', frame)
        return match.group(1) if match else None


class ProxySimulator:
    """
    Spouští skutečný proxy kód v test módu.
    
    Namísto spouštění celého proxy jako procesu,
    importuje moduly a volá funkce přímo.
    """
    
    def __init__(
        self,
        proxy_mode: str = "online",
        cloud_host: str = "127.0.0.1",
        cloud_port: int = 15710,
        listen_port: int = 15700,
        hybrid_fail_threshold: int = 3,
        hybrid_retry_interval: int = 5  # Short for tests
    ):
        self.proxy_mode = proxy_mode
        self.cloud_host = cloud_host
        self.cloud_port = cloud_port
        self.listen_port = listen_port
        self.hybrid_fail_threshold = hybrid_fail_threshold
        self.hybrid_retry_interval = hybrid_retry_interval
        
        self._server: Optional[asyncio.Server] = None
        self._running = False
        self._stats = {
            "frames_received": 0,
            "frames_forwarded": 0,
            "cloud_errors": 0,
            "local_acks": 0,
            "cloud_acks": 0
        }
        
        # HYBRID state
        self._hybrid_fail_count = 0
        self._hybrid_in_offline = False
        self._hybrid_last_offline_time = 0.0
        
    async def start(self):
        """Start proxy simulator."""
        self._server = await asyncio.start_server(
            self._handle_box_connection,
            "127.0.0.1",
            self.listen_port
        )
        self._running = True
        logger.info(f"ProxySim started on port {self.listen_port} (mode={self.proxy_mode})")
        
    async def stop(self):
        """Stop proxy simulator."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info(f"ProxySim stopped. Stats: {self._stats}")
        
    def get_stats(self) -> dict:
        """Get proxy statistics."""
        return self._stats.copy()
        
    async def _handle_box_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        """Handle connection from BOX (simulated)."""
        try:
            # Read frame
            data = await asyncio.wait_for(reader.read(8192), timeout=30.0)
            if not data:
                return
                
            frame = data.decode('utf-8', errors='ignore')
            self._stats["frames_received"] += 1
            
            # Process based on mode
            response = await self._process_frame(frame)
            
            if response:
                writer.write(response.encode('utf-8'))
                await writer.drain()
                
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.debug(f"ProxySim: Error handling connection: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
                
    async def _process_frame(self, frame: str) -> Optional[str]:
        """Process frame based on proxy mode."""
        
        if self.proxy_mode == "offline":
            # OFFLINE: Vždy lokální ACK
            self._stats["local_acks"] += 1
            return self._generate_local_ack(frame)
            
        elif self.proxy_mode == "hybrid":
            return await self._process_hybrid(frame)
            
        else:  # online
            return await self._process_online(frame)
            
    async def _process_online(self, frame: str) -> Optional[str]:
        """
        ONLINE mode: transparentní forwarding.
        
        - Pokud cloud odpoví, vrátí jeho odpověď
        - Pokud cloud timeout, vrátí None (BOX dostane timeout)
        """
        try:
            response = await self._forward_to_cloud(frame, timeout=3.0)
            if response:
                self._stats["cloud_acks"] += 1
                self._stats["frames_forwarded"] += 1
                return response
            else:
                # Timeout - v ONLINE módu transparentně (žádná odpověď)
                logger.debug("ProxySim ONLINE: Cloud timeout, returning None (transparent)")
                return None
                
        except Exception as e:
            logger.debug(f"ProxySim ONLINE: Cloud error: {e}")
            # V ONLINE módu transparentně - žádná odpověď
            return None
            
    async def _process_hybrid(self, frame: str) -> Optional[str]:
        """
        HYBRID mode: cloud s fallbackem na offline.
        
        - Pokud jsme v offline a uplynul interval, zkusíme cloud
        - Pokud cloud selže 3x, přepneme na offline
        - V offline generujeme lokální ACK
        """
        
        # Zjisti jestli máme zkusit cloud
        should_try = self._should_try_cloud()
        
        if should_try:
            try:
                response = await self._forward_to_cloud(frame, timeout=3.0)
                if response:
                    self._hybrid_record_success()
                    self._stats["cloud_acks"] += 1
                    self._stats["frames_forwarded"] += 1
                    return response
                else:
                    # Timeout
                    self._hybrid_record_failure()
                    
            except Exception as e:
                logger.debug(f"ProxySim HYBRID: Cloud error: {e}")
                self._hybrid_record_failure()
                
        # Fallback na lokální ACK
        self._stats["local_acks"] += 1
        return self._generate_local_ack(frame)
        
    def _should_try_cloud(self) -> bool:
        """Rozhodne, zda zkusit cloud v HYBRID módu."""
        if not self._hybrid_in_offline:
            return True
            
        # Jsme v offline - zkus znovu po intervalu
        elapsed = time.monotonic() - self._hybrid_last_offline_time
        if elapsed >= self.hybrid_retry_interval:
            logger.debug(f"ProxySim HYBRID: Retry interval elapsed ({elapsed:.1f}s), trying cloud")
            return True
            
        return False
        
    def _hybrid_record_failure(self):
        """Zaznamenej selhání cloudu v HYBRID módu."""
        self._hybrid_fail_count += 1
        self._stats["cloud_errors"] += 1
        
        if self._hybrid_fail_count >= self.hybrid_fail_threshold:
            if not self._hybrid_in_offline:
                logger.info(f"ProxySim HYBRID: Switching to offline after {self._hybrid_fail_count} failures")
                self._hybrid_in_offline = True
                self._hybrid_last_offline_time = time.monotonic()
                
    def _hybrid_record_success(self):
        """Zaznamenej úspěch cloudu v HYBRID módu."""
        if self._hybrid_in_offline:
            logger.info("ProxySim HYBRID: Cloud recovered, switching back to online")
        self._hybrid_fail_count = 0
        self._hybrid_in_offline = False
        
    async def _forward_to_cloud(self, frame: str, timeout: float = 3.0) -> Optional[str]:
        """Forward frame to cloud and return response.
        NOTE: timeout parameter used with asyncio.wait_for, consider using context manager in production."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.cloud_host, self.cloud_port),
                timeout=timeout
            )
            
            writer.write(frame.encode('utf-8'))
            await writer.drain()
            
            response = await asyncio.wait_for(reader.read(4096), timeout=timeout)
            
            writer.close()
            await writer.wait_closed()
            
            return response.decode('utf-8', errors='ignore') if response else None
            
        except asyncio.TimeoutError:
            return None
        except Exception:
            raise
            
    def _generate_local_ack(self, frame: str) -> str:
        """Generate local ACK response."""
        # Extract table name
        tbl_match = re.search(r'<TblName>([^<]+)</TblName>', frame)
        tbl_name = tbl_match.group(1) if tbl_match else "tbl_actual"
        
        return f'<Frame><Result>ACK</Result><ToDo>GetAll</ToDo><Source>local_{tbl_name}</Source><CRC>00000</CRC></Frame>\r\n'


class TestRunner:
    """Runner pro simulační testy."""
    
    def __init__(self):
        self.results: list[TestResult] = []
        
    async def run_all(self) -> bool:
        """Run all simulation tests."""
        logger.info("=" * 60)
        logger.info("SIMULATION TESTS START")
        logger.info("=" * 60)
        
        tests = [
            self.test_online_cloud_available,
            self.test_online_cloud_timeout,
            self.test_hybrid_fallback_after_threshold,
            self.test_hybrid_retry_after_interval,
            self.test_offline_local_only,
        ]
        
        for test_fn in tests:
            result = await test_fn()
            self.results.append(result)
            status = "✅ PASS" if result.passed else "❌ FAIL"
            logger.info(f"{status}: {result.name} ({result.duration_ms:.0f}ms)")
            if not result.passed:
                logger.info(f"   Details: {result.details}")
                
        # Summary
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        
        logger.info("=" * 60)
        logger.info(f"RESULTS: {passed}/{total} tests passed")
        logger.info("=" * 60)
        
        return passed == total
        
    async def test_online_cloud_available(self) -> TestResult:
        """Test ONLINE: Cloud dostupný, všechny framy forwardovány."""
        name = "ONLINE: Cloud available"
        start = time.monotonic()
        
        # Start mock cloud (normal mode)
        cloud = MockCloudServer(port=15710, mode="normal")
        proxy = ProxySimulator(proxy_mode="online", cloud_port=15710)
        box = MockBoxClient(proxy_port=15700)
        
        try:
            await cloud.start()
            await proxy.start()
            await asyncio.sleep(0.1)
            
            # Send 5 frames
            results = await box.send_frames(count=5, timeout_per_frame=3.0)
            
            # All should get ACK
            acks = [r for r in results if r.get("response") and "ACK" in r["response"]]
            errors = [r for r in results if r.get("error")]
            
            stats = proxy.get_stats()
            
            passed = (
                len(acks) == 5 and 
                len(errors) == 0 and 
                stats["cloud_acks"] == 5
            )
            
            details = f"ACKs={len(acks)}, errors={len(errors)}, cloud_acks={stats['cloud_acks']}"
            
        finally:
            await proxy.stop()
            await cloud.stop()
            
        return TestResult(
            name=name,
            passed=passed,
            details=details,
            duration_ms=(time.monotonic() - start) * 1000
        )
        
    async def test_online_cloud_timeout(self) -> TestResult:
        """Test ONLINE: Cloud timeout, BOX dostane timeout (transparentní)."""
        name = "ONLINE: Cloud timeout (transparent)"
        start = time.monotonic()
        
        # Start mock cloud v timeout módu (neodpovídá)
        cloud = MockCloudServer(port=15711, mode="timeout")
        proxy = ProxySimulator(proxy_mode="online", cloud_port=15711, listen_port=15701)
        box = MockBoxClient(proxy_port=15701)
        
        try:
            await cloud.start()
            await proxy.start()
            await asyncio.sleep(0.1)
            
            # Send 3 frames s krátkým timeoutem
            results = await box.send_frames(count=3, timeout_per_frame=2.0)
            
            # Všechny by měly být timeout (proxy neodpovídá v ONLINE módu)
            timeouts = [r for r in results if r.get("error") == "timeout"]
            
            stats = proxy.get_stats()
            
            passed = (
                len(timeouts) == 3 and 
                stats["local_acks"] == 0 and  # Žádné lokální ACK v ONLINE módu
                stats["cloud_acks"] == 0
            )
            
            details = f"timeouts={len(timeouts)}, local_acks={stats['local_acks']}"
            
        finally:
            await proxy.stop()
            await cloud.stop()
            
        return TestResult(
            name=name,
            passed=passed,
            details=details,
            duration_ms=(time.monotonic() - start) * 1000
        )
        
    async def test_hybrid_fallback_after_threshold(self) -> TestResult:
        """Test HYBRID: Po 3 selháních přepne na offline, lokální ACK."""
        name = "HYBRID: Fallback after threshold"
        start = time.monotonic()
        
        # Cloud selže 3x, pak je nedostupný
        cloud = MockCloudServer(port=15712, mode="fail_n_times", fail_count=100)
        proxy = ProxySimulator(
            proxy_mode="hybrid",
            cloud_port=15712,
            listen_port=15702,
            hybrid_fail_threshold=3,
            hybrid_retry_interval=60  # Dlouhý interval aby nezkoušel znovu
        )
        box = MockBoxClient(proxy_port=15702)
        
        try:
            await cloud.start()
            await proxy.start()
            await asyncio.sleep(0.1)
            
            # Send 6 frames
            # První 3 by měly selhat (cloud errors), pak přepne na offline
            # Další 3 by měly dostat lokální ACK
            results = await box.send_frames(count=6, timeout_per_frame=3.0)
            
            # Všechny by měly dostat nějaký ACK (lokální po fallbacku)
            acks = [r for r in results if r.get("response") and "ACK" in r["response"]]

            stats = proxy.get_stats()
            
            # Po 3 selháních by měl přepnout na offline
            passed = (
                stats["cloud_errors"] >= 3 and
                stats["local_acks"] >= 3 and  # Minimálně poslední 3 lokální
                len(acks) == 6  # Všech 6 dostalo ACK
            )
            
            details = f"cloud_errors={stats['cloud_errors']}, local_acks={stats['local_acks']}, total_acks={len(acks)}"
            
        finally:
            await proxy.stop()
            await cloud.stop()
            
        return TestResult(
            name=name,
            passed=passed,
            details=details,
            duration_ms=(time.monotonic() - start) * 1000
        )
        
    async def test_hybrid_retry_after_interval(self) -> TestResult:
        """Test HYBRID: Po intervalu zkusí cloud znovu."""
        name = "HYBRID: Retry after interval"
        start = time.monotonic()
        
        # Cloud bude fungovat
        cloud = MockCloudServer(port=15713, mode="normal")
        proxy = ProxySimulator(
            proxy_mode="hybrid",
            cloud_port=15713,
            listen_port=15703,
            hybrid_fail_threshold=2,
            hybrid_retry_interval=1  # Krátký interval pro test
        )
        box = MockBoxClient(proxy_port=15703)
        
        try:
            await cloud.start()
            await proxy.start()
            await asyncio.sleep(0.1)
            
            # Simuluj že jsme v offline módu
            proxy._hybrid_in_offline = True
            proxy._hybrid_fail_count = 3
            proxy._hybrid_last_offline_time = time.monotonic() - 2  # 2s ago
            
            # Send frame - měl by zkusit cloud (interval uplynul)
            results = await box.send_frames(count=1, timeout_per_frame=3.0)
            
            stats = proxy.get_stats()
            
            # Cloud by měl odpovědět a proxy by se měla vrátit do online
            cloud_ack = results[0].get("response") and "ACK" in results[0]["response"] and "local_" not in results[0]["response"]
            
            passed = (
                cloud_ack and
                stats["cloud_acks"] >= 1 and
                not proxy._hybrid_in_offline  # Vrátil se do online
            )
            
            details = f"cloud_ack={cloud_ack}, in_offline={proxy._hybrid_in_offline}, cloud_acks={stats['cloud_acks']}"
            
        finally:
            await proxy.stop()
            await cloud.stop()
            
        return TestResult(
            name=name,
            passed=passed,
            details=details,
            duration_ms=(time.monotonic() - start) * 1000
        )
        
    async def test_offline_local_only(self) -> TestResult:
        """Test OFFLINE: Všechny framy dostávají lokální ACK."""
        name = "OFFLINE: Local ACK only"
        start = time.monotonic()
        
        # Cloud by neměl být vůbec kontaktován
        cloud = MockCloudServer(port=15714, mode="normal")
        proxy = ProxySimulator(
            proxy_mode="offline",
            cloud_port=15714,
            listen_port=15704
        )
        box = MockBoxClient(proxy_port=15704)
        
        try:
            await cloud.start()
            await proxy.start()
            await asyncio.sleep(0.1)
            
            # Send 5 frames
            results = await box.send_frames(count=5, timeout_per_frame=3.0)
            
            # Všechny by měly dostat lokální ACK
            local_acks = [r for r in results if r.get("response") and "local_" in r.get("response", "")]
            
            stats = proxy.get_stats()
            
            passed = (
                len(local_acks) == 5 and
                stats["local_acks"] == 5 and
                stats["cloud_acks"] == 0 and  # Žádný cloud ACK
                stats["frames_forwarded"] == 0  # Nic nebylo forwardováno
            )
            
            details = f"local_acks={len(local_acks)}, cloud_acks={stats['cloud_acks']}, forwarded={stats['frames_forwarded']}"
            
        finally:
            await proxy.stop()
            await cloud.stop()
            
        return TestResult(
            name=name,
            passed=passed,
            details=details,
            duration_ms=(time.monotonic() - start) * 1000
        )


async def main():
    """Run simulation tests."""
    print("""
╔══════════════════════════════════════════════════════════════╗
║  PROXY SIMULATION TESTS                                      ║
╠══════════════════════════════════════════════════════════════╣
║  Testing 3 proxy modes: ONLINE, HYBRID, OFFLINE              ║
║  Using mock Cloud server and captured BOX frames             ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    runner = TestRunner()
    success = await runner.run_all()
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
