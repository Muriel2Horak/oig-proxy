#!/usr/bin/env python3
"""
OIG Proxy Network Diagnostic Tool.

Ověří síťovou konfiguraci a diagnostikuje problémy s připojením.

Kontroly:
1. DNS rozlišení cloud serveru
2. TCP spojení na cloud port
3. Traceroute ke cloudu
4. Latence (ping)
5. Test OIG protokolu (handshake)
6. Lokální síťová konfigurace
7. Firewall/NAT detekce

Použití:
    python network_diagnostic.py [--verbose] [--json] [--cloud HOST:PORT]
"""

import argparse
import asyncio
import json
import logging
import os
import platform
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Default cloud server
DEFAULT_CLOUD_HOST = "oigservis.cz"
DEFAULT_CLOUD_IP = "185.25.185.30"
DEFAULT_CLOUD_PORT = 5710

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)
logger = logging.getLogger("diagnostic")


@dataclass
class CheckResult:
    """Výsledek jedné kontroly."""
    name: str
    status: str  # "ok", "warning", "error", "info"
    message: str
    details: dict = field(default_factory=dict)
    duration_ms: float = 0.0


@dataclass
class DiagnosticReport:
    """Kompletní diagnostická zpráva."""
    timestamp: str
    hostname: str
    platform: str
    python_version: str
    cloud_target: str
    checks: list[CheckResult] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    
    def add_check(self, result: CheckResult):
        self.checks.append(result)
        
    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "hostname": self.hostname,
            "platform": self.platform,
            "python_version": self.python_version,
            "cloud_target": self.cloud_target,
            "checks": [asdict(c) for c in self.checks],
            "summary": self.summary
        }


class NetworkDiagnostic:
    """Síťová diagnostika pro OIG Proxy."""
    
    def __init__(
        self,
        cloud_host: str = DEFAULT_CLOUD_HOST,
        cloud_port: int = DEFAULT_CLOUD_PORT,
        verbose: bool = False
    ):
        self.cloud_host = cloud_host
        self.cloud_port = cloud_port
        self.verbose = verbose
        self.cloud_ip: Optional[str] = None
        
        self.report = DiagnosticReport(
            timestamp=datetime.now().isoformat(),
            hostname=socket.gethostname(),
            platform=f"{platform.system()} {platform.release()}",
            python_version=platform.python_version(),
            cloud_target=f"{cloud_host}:{cloud_port}"
        )
        
    def print_header(self):
        """Print diagnostic header."""
        print("""
╔══════════════════════════════════════════════════════════════╗
║          OIG PROXY NETWORK DIAGNOSTIC                        ║
╠══════════════════════════════════════════════════════════════╣
║  Diagnostikuje síťové připojení ke cloud serveru             ║
╚══════════════════════════════════════════════════════════════╝
""")
        print(f"  Timestamp:  {self.report.timestamp}")
        print(f"  Hostname:   {self.report.hostname}")
        print(f"  Platform:   {self.report.platform}")
        print(f"  Python:     {self.report.python_version}")
        print(f"  Cloud:      {self.cloud_host}:{self.cloud_port}")
        print()
        
    def print_result(self, result: CheckResult):
        """Print single check result."""
        icons = {
            "ok": "✅",
            "warning": "⚠️",
            "error": "❌",
            "info": "ℹ️"
        }
        icon = icons.get(result.status, "?")
        print(f"{icon} {result.name}: {result.message}")
        
        if self.verbose and result.details:
            for key, value in result.details.items():
                print(f"   └─ {key}: {value}")
                
    async def run_all_checks(self) -> DiagnosticReport:
        """Spustí všechny diagnostické kontroly."""
        self.print_header()
        print("=" * 60)
        print("RUNNING CHECKS...")
        print("=" * 60)
        print()
        
        checks = [
            self.check_dns_resolution,
            self.check_local_network,
            self.check_tcp_connection,
            self.check_ping_latency,
            self.check_traceroute,
            self.check_oig_protocol,
            self.check_proxy_port,
            self.check_firewall_hints,
        ]
        
        for check_fn in checks:
            try:
                result = await check_fn()
            except Exception as e:
                result = CheckResult(
                    name=check_fn.__name__,
                    status="error",
                    message=f"Check failed: {e}"
                )
            self.report.add_check(result)
            self.print_result(result)
            print()
            
        # Summary
        self._generate_summary()
        self._print_summary()
        
        return self.report
        
    async def check_dns_resolution(self) -> CheckResult:
        """Kontrola DNS rozlišení."""
        start = time.monotonic()
        name = "DNS Resolution"
        
        try:
            # Resolve hostname
            ip_addresses = socket.gethostbyname_ex(self.cloud_host)
            self.cloud_ip = ip_addresses[2][0] if ip_addresses[2] else None
            
            duration = (time.monotonic() - start) * 1000
            
            if self.cloud_ip:
                return CheckResult(
                    name=name,
                    status="ok",
                    message=f"{self.cloud_host} → {self.cloud_ip}",
                    details={
                        "hostname": self.cloud_host,
                        "ip": self.cloud_ip,
                        "all_ips": ip_addresses[2],
                        "aliases": ip_addresses[1]
                    },
                    duration_ms=duration
                )
            else:
                return CheckResult(
                    name=name,
                    status="error",
                    message=f"No IP found for {self.cloud_host}",
                    duration_ms=duration
                )
                
        except socket.gaierror as e:
            return CheckResult(
                name=name,
                status="error",
                message=f"DNS resolution failed: {e}",
                details={"error": str(e)}
            )
            
    async def check_local_network(self) -> CheckResult:
        """Kontrola lokální síťové konfigurace."""
        name = "Local Network"
        
        try:
            # Get local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            
            # Check if private IP
            is_private = (
                local_ip.startswith("192.168.") or
                local_ip.startswith("10.") or
                local_ip.startswith("172.")
            )
            
            # Get hostname
            hostname = socket.gethostname()
            
            details = {
                "local_ip": local_ip,
                "hostname": hostname,
                "is_private_ip": is_private
            }
            
            # Try to get default gateway (platform specific)
            gateway = self._get_default_gateway()
            if gateway:
                details["gateway"] = gateway
                
            return CheckResult(
                name=name,
                status="ok",
                message=f"Local IP: {local_ip}",
                details=details
            )
            
        except Exception as e:
            return CheckResult(
                name=name,
                status="error",
                message=f"Cannot determine local network: {e}"
            )
            
    def _get_default_gateway(self) -> Optional[str]:
        """Get default gateway (platform specific)."""
        try:
            if platform.system() == "Darwin":  # macOS
                result = subprocess.run(
                    ["route", "-n", "get", "default"],
                    capture_output=True, text=True, timeout=5
                )
                match = re.search(r"gateway:\s+(\S+)", result.stdout)
                return match.group(1) if match else None
                
            elif platform.system() == "Linux":
                result = subprocess.run(
                    ["ip", "route", "show", "default"],
                    capture_output=True, text=True, timeout=5
                )
                match = re.search(r"via\s+(\S+)", result.stdout)
                return match.group(1) if match else None
                
        except Exception:
            pass
        return None
        
    async def check_tcp_connection(self) -> CheckResult:
        """Test TCP spojení na cloud port."""
        start = time.monotonic()
        name = "TCP Connection"
        
        target_ip = self.cloud_ip or DEFAULT_CLOUD_IP
        
        try:
            # Try to connect
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target_ip, self.cloud_port),
                timeout=10.0
            )
            
            duration = (time.monotonic() - start) * 1000
            
            # Get connection info
            peer = writer.get_extra_info('peername')
            local = writer.get_extra_info('sockname')
            
            writer.close()
            await writer.wait_closed()
            
            return CheckResult(
                name=name,
                status="ok",
                message=f"Connected to {target_ip}:{self.cloud_port} ({duration:.0f}ms)",
                details={
                    "target": f"{target_ip}:{self.cloud_port}",
                    "local_endpoint": f"{local[0]}:{local[1]}",
                    "connect_time_ms": round(duration, 1)
                },
                duration_ms=duration
            )
            
        except asyncio.TimeoutError:
            return CheckResult(
                name=name,
                status="error",
                message=f"Connection timeout to {target_ip}:{self.cloud_port}",
                details={
                    "possible_causes": [
                        "Firewall blocking outbound port 5710",
                        "Cloud server down",
                        "Network routing issue"
                    ]
                }
            )
        except ConnectionRefusedError:
            return CheckResult(
                name=name,
                status="error",
                message=f"Connection refused by {target_ip}:{self.cloud_port}",
                details={
                    "possible_causes": [
                        "Cloud server not listening",
                        "Wrong port number"
                    ]
                }
            )
        except Exception as e:
            return CheckResult(
                name=name,
                status="error",
                message=f"Connection failed: {e}"
            )
            
    async def check_ping_latency(self) -> CheckResult:
        """Měření latence pomocí ping."""
        name = "Ping Latency"
        target = self.cloud_ip or DEFAULT_CLOUD_IP
        
        try:
            # Platform specific ping
            if platform.system() == "Windows":
                cmd = ["ping", "-n", "3", target]
            else:
                cmd = ["ping", "-c", "3", "-W", "5", target]
                
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20
            )
            
            # Parse output
            if platform.system() == "Windows":
                match = re.search(r"Average\s*=\s*(\d+)ms", result.stdout)
            else:
                match = re.search(r"avg\s*=\s*[\d.]+/([\d.]+)/", result.stdout)
                
            if result.returncode == 0 and match:
                avg_ms = float(match.group(1))
                
                status = "ok" if avg_ms < 100 else "warning"
                
                return CheckResult(
                    name=name,
                    status=status,
                    message=f"Average latency: {avg_ms:.1f}ms",
                    details={
                        "target": target,
                        "avg_latency_ms": avg_ms,
                        "quality": "good" if avg_ms < 50 else "acceptable" if avg_ms < 100 else "high"
                    }
                )
            else:
                return CheckResult(
                    name=name,
                    status="warning",
                    message="Ping failed or ICMP blocked",
                    details={
                        "note": "ICMP may be blocked, TCP connection still works"
                    }
                )
                
        except subprocess.TimeoutExpired:
            return CheckResult(
                name=name,
                status="warning",
                message="Ping timeout",
                details={"note": "ICMP may be blocked"}
            )
        except Exception as e:
            return CheckResult(
                name=name,
                status="warning",
                message=f"Cannot ping: {e}"
            )
            
    async def check_traceroute(self) -> CheckResult:
        """Traceroute ke cloudu."""
        name = "Traceroute"
        target = self.cloud_ip or DEFAULT_CLOUD_IP
        
        try:
            # Platform specific traceroute
            if platform.system() == "Windows":
                cmd = ["tracert", "-d", "-h", "15", target]
            elif platform.system() == "Darwin":
                cmd = ["traceroute", "-n", "-m", "15", "-w", "2", target]
            else:
                cmd = ["traceroute", "-n", "-m", "15", "-w", "2", target]
                
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            # Parse hops
            hops = []
            for line in result.stdout.split("\n"):
                # Match lines like "1  192.168.1.1  1.234 ms"
                hop_match = re.search(r"^\s*(\d+)\s+(\S+)", line)
                if hop_match:
                    hop_num = int(hop_match.group(1))
                    hop_ip = hop_match.group(2)
                    if hop_ip != "*":
                        hops.append({"hop": hop_num, "ip": hop_ip})
                        
            if hops:
                return CheckResult(
                    name=name,
                    status="ok",
                    message=f"Route: {len(hops)} hops to destination",
                    details={
                        "target": target,
                        "hop_count": len(hops),
                        "hops": hops[:10],  # First 10 hops
                        "reached_target": any(h["ip"] == target for h in hops)
                    }
                )
            else:
                return CheckResult(
                    name=name,
                    status="warning",
                    message="Traceroute incomplete",
                    details={"output": result.stdout[:500]}
                )
                
        except subprocess.TimeoutExpired:
            return CheckResult(
                name=name,
                status="warning",
                message="Traceroute timeout (may be blocked)"
            )
        except FileNotFoundError:
            return CheckResult(
                name=name,
                status="info",
                message="Traceroute not available on this system"
            )
        except Exception as e:
            return CheckResult(
                name=name,
                status="warning",
                message=f"Traceroute failed: {e}"
            )
            
    async def check_oig_protocol(self) -> CheckResult:
        """Test OIG protokolu - pošle testovací frame."""
        name = "OIG Protocol"
        target_ip = self.cloud_ip or DEFAULT_CLOUD_IP
        
        # Testovací frame (minimální validní frame)
        test_frame = (
            '<Frame><TblName>tbl_box</TblName>'
            '<ID_Device>0000000000</ID_Device>'
            '<Reason>Diagnostic</Reason>'
            '<ver>00000</ver><CRC>00000</CRC></Frame>\r\n'
        )
        
        try:
            start = time.monotonic()
            
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target_ip, self.cloud_port),
                timeout=10.0
            )
            
            # Send test frame
            writer.write(test_frame.encode('utf-8'))
            await writer.drain()
            
            # Wait for response (cloud may send ACK or close)
            try:
                response = await asyncio.wait_for(
                    reader.read(4096),
                    timeout=5.0
                )
                response_text = response.decode('utf-8', errors='ignore')
                duration = (time.monotonic() - start) * 1000
                
                writer.close()
                await writer.wait_closed()
                
                # Check response
                if "ACK" in response_text or "NACK" in response_text:
                    return CheckResult(
                        name=name,
                        status="ok",
                        message=f"Cloud responded ({duration:.0f}ms)",
                        details={
                            "response_preview": response_text[:200],
                            "response_length": len(response_text),
                            "round_trip_ms": round(duration, 1)
                        },
                        duration_ms=duration
                    )
                elif response_text:
                    return CheckResult(
                        name=name,
                        status="warning",
                        message=f"Unexpected response ({duration:.0f}ms)",
                        details={
                            "response_preview": response_text[:200]
                        },
                        duration_ms=duration
                    )
                else:
                    return CheckResult(
                        name=name,
                        status="warning",
                        message="No response from cloud",
                        details={
                            "note": "Cloud may have closed connection"
                        }
                    )
                    
            except asyncio.TimeoutError:
                duration = (time.monotonic() - start) * 1000
                writer.close()
                return CheckResult(
                    name=name,
                    status="warning",
                    message=f"No response within 5s (connection OK)",
                    details={
                        "note": "Cloud accepted connection but didn't respond to test frame"
                    },
                    duration_ms=duration
                )
                
        except Exception as e:
            return CheckResult(
                name=name,
                status="error",
                message=f"Protocol test failed: {e}"
            )
            
    async def check_proxy_port(self) -> CheckResult:
        """Kontrola zda proxy port není blokován."""
        name = "Proxy Port (5710)"
        
        try:
            # Check if anything is listening on 5710 locally
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 5710))
            sock.close()
            
            if result == 0:
                return CheckResult(
                    name=name,
                    status="ok",
                    message="Proxy is listening on port 5710",
                    details={
                        "local_port": 5710,
                        "status": "listening"
                    }
                )
            else:
                return CheckResult(
                    name=name,
                    status="info",
                    message="Proxy not running on port 5710",
                    details={
                        "note": "This is expected if running diagnostic standalone"
                    }
                )
                
        except Exception as e:
            return CheckResult(
                name=name,
                status="info",
                message=f"Cannot check proxy port: {e}"
            )
            
    async def check_firewall_hints(self) -> CheckResult:
        """Detekce možných firewall problémů."""
        name = "Firewall Hints"
        
        hints = []
        
        # Check outbound 5710
        target_ip = self.cloud_ip or DEFAULT_CLOUD_IP
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((target_ip, self.cloud_port))
            sock.close()
            
            if result != 0:
                hints.append(f"Outbound port {self.cloud_port} may be blocked")
        except Exception:
            hints.append(f"Cannot test outbound port {self.cloud_port}")
            
        # Check common DNS
        try:
            socket.gethostbyname("google.com")
        except Exception:
            hints.append("DNS resolution issues detected")
            
        # Platform specific firewall check
        if platform.system() == "Darwin":  # macOS
            try:
                result = subprocess.run(
                    ["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"],
                    capture_output=True, text=True, timeout=5
                )
                if "enabled" in result.stdout.lower():
                    hints.append("macOS firewall is enabled")
            except Exception:
                pass
                
        elif platform.system() == "Linux":
            try:
                result = subprocess.run(
                    ["iptables", "-L", "-n"],
                    capture_output=True, text=True, timeout=5
                )
                if "DROP" in result.stdout or "REJECT" in result.stdout:
                    hints.append("iptables has DROP/REJECT rules")
            except Exception:
                pass
                
        if hints:
            return CheckResult(
                name=name,
                status="warning",
                message=f"Found {len(hints)} potential issues",
                details={"hints": hints}
            )
        else:
            return CheckResult(
                name=name,
                status="ok",
                message="No obvious firewall issues detected"
            )
            
    def _generate_summary(self):
        """Generate summary of all checks."""
        ok_count = sum(1 for c in self.report.checks if c.status == "ok")
        warning_count = sum(1 for c in self.report.checks if c.status == "warning")
        error_count = sum(1 for c in self.report.checks if c.status == "error")
        
        # Determine overall status
        if error_count > 0:
            overall = "PROBLEMS DETECTED"
            recommendation = "Check the errors above and verify network configuration"
        elif warning_count > 0:
            overall = "MOSTLY OK"
            recommendation = "Minor issues detected, proxy should work"
        else:
            overall = "ALL OK"
            recommendation = "Network configuration looks good"
            
        self.report.summary = {
            "overall_status": overall,
            "checks_ok": ok_count,
            "checks_warning": warning_count,
            "checks_error": error_count,
            "recommendation": recommendation
        }
        
    def _print_summary(self):
        """Print summary."""
        print()
        print("=" * 60)
        print("SUMMARY")
        print("=" * 60)
        
        s = self.report.summary
        
        status_icon = {
            "ALL OK": "✅",
            "MOSTLY OK": "⚠️",
            "PROBLEMS DETECTED": "❌"
        }.get(s["overall_status"], "?")
        
        print(f"\n{status_icon} Overall: {s['overall_status']}")
        print(f"   ✅ OK: {s['checks_ok']}")
        print(f"   ⚠️ Warnings: {s['checks_warning']}")
        print(f"   ❌ Errors: {s['checks_error']}")
        print(f"\n💡 Recommendation: {s['recommendation']}")
        print()


async def main():
    parser = argparse.ArgumentParser(
        description="OIG Proxy Network Diagnostic Tool"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )
    parser.add_argument(
        "--cloud",
        default=f"{DEFAULT_CLOUD_HOST}:{DEFAULT_CLOUD_PORT}",
        help=f"Cloud server (default: {DEFAULT_CLOUD_HOST}:{DEFAULT_CLOUD_PORT})"
    )
    parser.add_argument(
        "--output", "-o",
        help="Save report to file"
    )
    
    args = parser.parse_args()
    
    # Parse cloud host:port
    if ":" in args.cloud:
        host, port = args.cloud.rsplit(":", 1)
        port = int(port)
    else:
        host = args.cloud
        port = DEFAULT_CLOUD_PORT
        
    # Run diagnostic
    diag = NetworkDiagnostic(
        cloud_host=host,
        cloud_port=port,
        verbose=args.verbose
    )
    
    report = await diag.run_all_checks()
    
    # Output
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        
    if args.output:
        output_path = Path(args.output)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"\n📁 Report saved to: {output_path}")
        
    # Exit code
    if report.summary.get("checks_error", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
