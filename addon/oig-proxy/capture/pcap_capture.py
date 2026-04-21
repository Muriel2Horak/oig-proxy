#!/usr/bin/env python3
"""
PCAP TCP capture pro OIG Proxy v2.

Spouští tcpdump jako subprocess a zapisuje raw TCP provoz do .pcap souboru.
Zachytává obousměrnou komunikaci na proxy portu (box ↔ cloud).

Použití:
    pc = PcapCapture(
        port=5710,
        pcap_path="/data/capture.pcap",
        interface="any",
    )
    pc.start()
    ...
    pc.stop()
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess  # nosec: B404 - intentional infrastructure tool for packet capture
from pathlib import Path

logger = logging.getLogger(__name__)

# Výchozí cesta k tcpdump binárce
_TCPDUMP_CANDIDATES = ["/usr/sbin/tcpdump", "/usr/bin/tcpdump", "tcpdump"]


def _find_tcpdump() -> str | None:
    for candidate in _TCPDUMP_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


class PcapCapture:
    """
    PCAP TCP capture pomocí tcpdump subprocess.

    Zachytává veškerý TCP provoz na daném portu a zapisuje do .pcap souboru.
    Rotuje soubory po překročení max_size_mb (pokud > 0).
    """

    def __init__(
        self,
        port: int = 5710,
        pcap_path: str = "/data/capture.pcap",
        interface: str = "any",
        max_size_mb: int = 100,
        snaplen: int = 65535,
    ) -> None:
        self.port = port
        self.pcap_path = pcap_path
        self.interface = interface
        self.max_size_mb = max_size_mb
        self.snaplen = snaplen

        self._process: subprocess.Popen[bytes] | None = None
        self._monitor_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Spustí tcpdump subprocess. Volá se ze synchronního kontextu (startup)."""
        tcpdump = _find_tcpdump()
        if tcpdump is None:
            logger.warning("PcapCapture: tcpdump not found – PCAP capture disabled")
            return

        # Ujistíme se, že adresář existuje
        pcap_dir = str(Path(self.pcap_path).parent)
        os.makedirs(pcap_dir, exist_ok=True)

        # Sestavíme příkaz
        cmd = self._build_cmd(tcpdump)
        logger.info("PcapCapture starting: %s", " ".join(cmd))

        try:
            # B603: subprocess.Popen is used intentionally for tcpdump capture; no user input involved
            self._process = subprocess.Popen(  # nosec: B603
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                close_fds=True,
            )
            logger.info(
                "PcapCapture started: pid=%d port=%d file=%s",
                self._process.pid,
                self.port,
                self.pcap_path,
            )
        except (OSError, ValueError) as exc:
            logger.warning("PcapCapture failed to start tcpdump: %s", exc)
            self._process = None

    async def start_async(self) -> None:
        """Spustí tcpdump a spustí asyncio monitoring task."""
        self.start()
        if self._process is not None:
            loop = asyncio.get_running_loop()
            self._monitor_task = loop.create_task(
                self._monitor_process(),
                name="pcap_monitor",
            )

    def stop(self) -> None:
        """Zastaví tcpdump subprocess gracefully."""
        if self._monitor_task is not None and not self._monitor_task.done():
            self._monitor_task.cancel()
            self._monitor_task = None

        if self._process is not None:
            pid = self._process.pid
            try:
                self._process.send_signal(signal.SIGTERM)
                try:
                    self._process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2.0)
                logger.info("PcapCapture stopped: pid=%d", pid)
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.debug("PcapCapture stop error: %s", exc)
            finally:
                self._process = None

    @property
    def is_running(self) -> bool:
        """Vrátí True pokud tcpdump subprocess běží."""
        if self._process is None:
            return False
        return self._process.poll() is None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_cmd(self, tcpdump: str) -> list[str]:
        """Sestaví tcpdump příkaz."""
        cmd = [
            tcpdump,
            "-i", self.interface,
            "-s", str(self.snaplen),
            "-w", self.pcap_path,
            # BPF filter: TCP provoz na proxy portu (obě směry)
            f"tcp port {self.port}",
        ]

        # Rotace dle velikosti (tcpdump -C v MB)
        if self.max_size_mb > 0:
            cmd.extend(["-C", str(self.max_size_mb)])

        return cmd

    async def _monitor_process(self) -> None:
        """Asyncio task – monitoruje subprocess a loguje jeho ukončení."""
        if self._process is None:
            return
        try:
            while True:
                await asyncio.sleep(5.0)
                if self._process is None:
                    break
                retcode = self._process.poll()
                if retcode is not None:
                    # tcpdump skončil
                    stderr_out = b""
                    if self._process.stderr:
                        try:
                            stderr_out = self._process.stderr.read(2048)
                        except OSError:
                            pass
                    if retcode == 0:
                        logger.info("PcapCapture: tcpdump exited normally")
                    else:
                        logger.warning(
                            "PcapCapture: tcpdump exited with code %d: %s",
                            retcode,
                            stderr_out.decode("utf-8", errors="replace").strip(),
                        )
                    self._process = None
                    break
        except asyncio.CancelledError:
            raise
