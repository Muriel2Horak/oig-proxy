#!/usr/bin/env python3
"""
Test: Replay starého Setting frame na BOX.

Účel: Zjistit jestli BOX validuje čas (DT/TSec) nebo akceptuje starý příkaz.

Jak spustit:
1. Na HA serveru: zastav proxy addon
2. Spusť tento skript (např. přes SSH tunel)
3. BOX se připojí a pošle IsNewSet
4. Skript odpoví starým Setting framem z 7.12.2025
5. Uvidíme jestli BOX pošle ACK nebo NACK

Výsledky:
- ACK → BOX nevaliduje čas, replay funguje!
- NACK (Reason=WC) → CRC problém (nemělo by se stát)
- NACK (Reason=???) → BOX validuje čas nebo ID_Set
- Timeout → BOX ignoruje příkaz
"""
# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,logging-fstring-interpolation,broad-exception-caught,unspecified-encoding,import-outside-toplevel,unused-import,unused-argument,too-many-locals,too-many-statements,too-many-branches,too-many-instance-attributes,f-string-without-interpolation,line-too-long,too-many-nested-blocks,too-many-return-statements,no-else-return,unused-variable,no-else-continue,duplicate-code

import asyncio
import logging
import re
import sys
from datetime import datetime

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [TEST] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


# Zachycený validní Setting frame z 7.12.2025 (MODE=3, No Limit)
# Původní timestamp je 4 dny starý - testujeme jestli BOX validuje čas
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
    '</Frame>'
)

# END frame po Setting session
END_FRAME = (
    '<Frame>'
    '<Result>END</Result>'
    '<Time>2025-12-11 12:00:00</Time>'
    '<UTCTime>2025-12-11 11:00:00</UTCTime>'
    '<ToDo>GetActual</ToDo>'
    '<CRC>28606</CRC>'
    '</Frame>'
)

# Standard ACK pro ostatní frames
DEFAULT_ACK = '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>'


class ReplayTestServer:
    """Test server pro replay Setting frame."""

    def __init__(self, host: str = "0.0.0.0", port: int = 5710):
        self.host = host
        self.port = port
        self.test_result = None
        self.frames_log = []

    async def handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        addr = writer.get_extra_info('peername')
        logger.info(f"🔌 BOX připojen: {addr}")

        setting_sent = False

        try:
            while True:
                data = await asyncio.wait_for(reader.read(4096), timeout=120.0)
                if not data:
                    break

                text = data.decode("utf-8", errors="ignore")
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                # Log přijatý frame
                self.frames_log.append({"dir": "BOX→", "data": text})

                # Detekce typu zprávy
                is_new_set = "<Result>IsNewSet</Result>" in text
                is_ack = "<Result>ACK</Result>" in text
                is_nack = "<Result>NACK</Result>" in text

                # Parse table name
                tbl_match = re.search(r'<TblName>([^<]+)</TblName>', text)
                table_name = tbl_match.group(1) if tbl_match else "unknown"

                # Parse Result
                result_match = re.search(r'<Result>([^<]+)</Result>', text)
                result = result_match.group(1) if result_match else None

                # Parse Reason (pro ACK/NACK)
                reason_match = re.search(r'<Reason>([^<]+)</Reason>', text)
                reason = reason_match.group(1) if reason_match else None

                logger.info(f"📥 {timestamp} BOX→: {result or table_name} {f'(Reason={reason})' if reason else ''}")

                # === HLAVNÍ LOGIKA ===

                if is_new_set and not setting_sent:
                    # BOX se ptá na nová nastavení → pošleme starý Setting frame
                    logger.info("=" * 60)
                    logger.info("🎯 IsNewSet detekován! Posílám starý Setting frame (MODE=3)...")
                    logger.info(f"   Timestamp v frame: 07.12.2025 20:41:21 (4 dny starý)")
                    logger.info("=" * 60)

                    writer.write(SETTING_FRAME_MODE3.encode('utf-8'))
                    await writer.drain()
                    self.frames_log.append({"dir": "→BOX", "data": SETTING_FRAME_MODE3})
                    logger.info(f"📤 {timestamp} →BOX: Setting (MODE=3, CRC=16664)")

                    setting_sent = True

                elif is_ack and setting_sent and reason == "Setting":
                    # BOX potvrdil Setting!
                    logger.info("=" * 60)
                    logger.info("✅ SUCCESS! BOX přijal starý Setting frame!")
                    logger.info("   → BOX NEVALIDUJE ČAS - replay funguje!")
                    logger.info("=" * 60)
                    self.test_result = "SUCCESS"

                    # Pošleme END frame
                    writer.write(END_FRAME.encode('utf-8'))
                    await writer.drain()
                    logger.info(f"📤 {timestamp} →BOX: END frame")

                elif is_nack and setting_sent:
                    # BOX odmítl Setting
                    logger.info("=" * 60)
                    logger.info(f"❌ FAIL! BOX odmítl Setting frame!")
                    logger.info(f"   Reason: {reason}")
                    if reason == "WC":
                        logger.info("   → Špatné CRC (nemělo by se stát u replay)")
                    else:
                        logger.info(f"   → Možná validace času nebo ID_Set")
                    logger.info("=" * 60)
                    self.test_result = f"FAIL:{reason}"

                    # Pošleme END frame
                    writer.write(END_FRAME.encode('utf-8'))
                    await writer.drain()

                else:
                    # Ostatní frames - standardní ACK
                    writer.write(DEFAULT_ACK.encode('utf-8'))
                    await writer.drain()
                    logger.debug(f"📤 {timestamp} →BOX: ACK")

        except asyncio.TimeoutError:
            logger.warning("⏱️ Timeout - BOX neodpověděl")
            if setting_sent and self.test_result is None:
                self.test_result = "TIMEOUT"
        except Exception as e:
            logger.error(f"❌ Error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()
            logger.info("🔌 Spojení ukončeno")

    async def run(self, timeout: int = 180):
        """Spustí test server a čeká na výsledek."""
        server = await asyncio.start_server(
            self.handle_connection, self.host, self.port
        )

        addr = server.sockets[0].getsockname()
        logger.info(f"🟢 Test server listening on {addr}")
        logger.info(f"   Čekám na BOX připojení (timeout {timeout}s)...")
        logger.info("")
        logger.info("📋 Test: Replay starého Setting frame (07.12.2025)")
        logger.info("   Očekávání: BOX buď přijme (ACK) nebo odmítne (NACK)")
        logger.info("")

        try:
            async with asyncio.timeout(timeout):
                while self.test_result is None:
                    await asyncio.sleep(1)
        except asyncio.TimeoutError:
            if self.test_result is None:
                logger.warning("⏱️ Globální timeout - žádné připojení od BOXu")
                self.test_result = "NO_CONNECTION"

        server.close()
        await server.wait_closed()

        # Výsledek
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"📊 VÝSLEDEK TESTU: {self.test_result}")
        logger.info("=" * 60)

        return self.test_result


async def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║  TEST: Replay starého Setting frame na OIG BOX               ║
╠══════════════════════════════════════════════════════════════╣
║  Tento test zjistí jestli BOX validuje čas v Setting frame.  ║
║                                                              ║
║  Před spuštěním:                                             ║
║  1. Zastav proxy addon na HA serveru                         ║
║  2. Spusť tento skript (na HA nebo přes SSH tunel)           ║
║                                                              ║
║  Frame k testu: MODE=3 z 07.12.2025 (4 dny starý)            ║
╚══════════════════════════════════════════════════════════════╝
""")

    server = ReplayTestServer()
    result = await server.run(timeout=180)

    return 0 if result == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
