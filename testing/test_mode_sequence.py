#!/usr/bin/env python3
"""
Test: Sekvence změn MODE s čekáním na potvrzení.

Scénář:
1. Test A: Replay MODE→3 (starý frame) → čekej na ACK + tbl_events
2. Pauza 60s
3. Test B: Replay MODE→0 (starý frame) → čekej na ACK + tbl_events
4. Pauza 60s
5. Test C: Modifikovaný frame (změň NewValue, zachovej CRC)
6. Pauza 60s
7. Test D: Návrat na MODE→0

Běží jako MITM proxy - vše forwarduje, injektuje naše framy.
Loguje jen důležité eventy + statistiku každých 15s.
"""
# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,logging-fstring-interpolation,broad-exception-caught,unspecified-encoding,import-outside-toplevel,unused-import,unused-argument,too-many-locals,too-many-statements,too-many-branches,too-many-instance-attributes,f-string-without-interpolation,line-too-long,too-many-nested-blocks,too-many-return-statements,no-else-return,unused-variable,no-else-continue,duplicate-code

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List
from collections import defaultdict

OUTPUT_DIR = "/tmp/mode_sequence_test"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Méně verbose logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TEST] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{OUTPUT_DIR}/test.log")
    ]
)
logger = logging.getLogger(__name__)

CLOUD_HOST = "185.25.185.30"
CLOUD_PORT = 5710

# === TESTOVACÍ FRAMY ===

FRAME_MODE_0 = (
    '<Frame><ID>13584151</ID><ID_Device>2206237016</ID_Device>'
    '<ID_Set>1765135114</ID_Set><ID_SubD>0</ID_SubD>'
    '<DT>07.12.2025 20:18:34</DT><NewValue>0</NewValue>'
    '<Confirm>New</Confirm><TblName>tbl_box_prms</TblName>'
    '<TblItem>MODE</TblItem><ID_Server>5</ID_Server>'
    '<mytimediff>0</mytimediff><Reason>Setting</Reason>'
    '<TSec>2025-12-07 19:46:54</TSec><ver>10918</ver>'
    '<CRC>47999</CRC></Frame>\r\n'
)

FRAME_MODE_3 = (
    '<Frame><ID>13584153</ID><ID_Device>2206237016</ID_Device>'
    '<ID_Set>1765135503</ID_Set><ID_SubD>0</ID_SubD>'
    '<DT>07.12.2025 20:25:03</DT><NewValue>3</NewValue>'
    '<Confirm>New</Confirm><TblName>tbl_box_prms</TblName>'
    '<TblItem>MODE</TblItem><ID_Server>5</ID_Server>'
    '<mytimediff>0</mytimediff><Reason>Setting</Reason>'
    '<TSec>2025-12-07 19:46:59</TSec><ver>23912</ver>'
    '<CRC>30080</CRC></Frame>\r\n'
)


@dataclass
class TestStep:
    name: str
    frame: str
    description: str
    pause_after: int = 60
    injected: bool = False
    ack_received: bool = False
    event_received: bool = False
    event_content: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


@dataclass
class TestState:
    steps: List[TestStep] = field(default_factory=list)
    current_step_idx: int = 0
    waiting_for_isnewset: bool = True
    test_completed: bool = False
    captured_frames: List[dict] = field(default_factory=list)

    @property
    def current_step(self) -> Optional[TestStep]:
        if 0 <= self.current_step_idx < len(self.steps):
            return self.steps[self.current_step_idx]
        return None

    def advance_step(self):
        self.current_step_idx += 1
        self.waiting_for_isnewset = True
        if self.current_step_idx >= len(self.steps):
            self.test_completed = True


def modify_frame(base_frame: str, **changes) -> str:
    result = base_frame
    for tag, new_value in changes.items():
        pattern = f'<{tag}>[^<]*</{tag}>'
        replacement = f'<{tag}>{new_value}</{tag}>'
        result = re.sub(pattern, replacement, result)
    return result


def create_test_steps() -> List[TestStep]:
    steps = []

    steps.append(TestStep(
        name="A: Replay MODE→3",
        frame=FRAME_MODE_3,
        description="Starý frame z 7.12., MODE=3",
        pause_after=60
    ))

    steps.append(TestStep(
        name="B: Replay MODE→0",
        frame=FRAME_MODE_0,
        description="Starý frame z 7.12., MODE=0",
        pause_after=60
    ))

    # Test C: Modifikovaný frame - změň NewValue ale zachovej CRC
    modified_c = modify_frame(FRAME_MODE_3, NewValue="5")
    steps.append(TestStep(
        name="C: Modified MODE→5",
        frame=modified_c,
        description="Změněn NewValue na 5, původní CRC",
        pause_after=60
    ))

    steps.append(TestStep(
        name="D: Cleanup MODE→0",
        frame=FRAME_MODE_0,
        description="Návrat na MODE=0",
        pause_after=10
    ))

    return steps


class ModeSequenceTest:
    def __init__(self, listen_port: int = 5710):
        self.listen_port = listen_port
        self.state = TestState(steps=create_test_steps())
        self.frame_counter = 0

        # Statistiky pro tiché logování
        self.stats = defaultdict(int)
        self.last_stats_time = datetime.now()
        self.stats_interval = 15  # sekund

    def log_stats(self, force: bool = False):
        """Vypíše statistiku každých N sekund."""
        now = datetime.now()
        elapsed = (now - self.last_stats_time).total_seconds()

        if force or elapsed >= self.stats_interval:
            if self.stats:
                parts = [f"{k}:{v}" for k, v in sorted(self.stats.items())]
                step = self.state.current_step
                step_info = f"[{step.name}]" if step else "[done]"
                logger.info(f"📊 {step_info} {', '.join(parts)}")
                self.stats.clear()
            self.last_stats_time = now

    def save_frame(self, direction: str, data: str, frame_type: str = ""):
        self.frame_counter += 1
        ts = datetime.now().isoformat()

        self.state.captured_frames.append({
            "id": self.frame_counter,
            "timestamp": ts,
            "direction": direction,
            "type": frame_type,
            "length": len(data),
            "data": data[:500] + ("..." if len(data) > 500 else "")
        })

        # Uložit do souboru
        filename = f"{OUTPUT_DIR}/frame_{self.frame_counter:04d}_{direction}.xml"
        with open(filename, "w") as f:
            f.write(f"<!-- {ts} | {direction} | {frame_type} -->\n")
            f.write(data)

        # Statistika - neloguj jednotlivě
        self.stats[frame_type] += 1
        self.log_stats()

    def save_results(self):
        results = {
            "test_time": datetime.now().isoformat(),
            "steps": []
        }

        for step in self.state.steps:
            results["steps"].append({
                "name": step.name,
                "description": step.description,
                "injected": step.injected,
                "ack_received": step.ack_received,
                "event_received": step.event_received,
                "event_content": step.event_content,
                "success": step.ack_received and step.event_received
            })

        filename = f"{OUTPUT_DIR}/results.json"
        with open(filename, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        # Shrnutí na konzoli
        print("\n" + "="*60)
        print("📊 SHRNUTÍ VÝSLEDKŮ")
        print("="*60)
        for step in self.state.steps:
            status = "✅" if (step.ack_received and step.event_received) else "❌"
            ack = "✓" if step.ack_received else "✗"
            evt = "✓" if step.event_received else "✗"
            print(f"{status} {step.name}: ACK={ack} Event={evt}")
            if step.event_content:
                print(f"   └─ {step.event_content}")
        print("="*60)
        print(f"📁 Výsledky: {filename}")
        print(f"📁 Framy: {OUTPUT_DIR}/frame_*.xml")

    async def handle_box(self, box_reader, box_writer):
        addr = box_writer.get_extra_info('peername')
        logger.info(f"🔌 BOX připojen: {addr}")

        try:
            cloud_reader, cloud_writer = await asyncio.wait_for(
                asyncio.open_connection(CLOUD_HOST, CLOUD_PORT),
                timeout=10.0
            )
            logger.info(f"☁️ Připojeno na cloud {CLOUD_HOST}:{CLOUD_PORT}")
        except Exception as e:
            logger.error(f"❌ Cloud connection failed: {e}")
            box_writer.close()
            return

        try:
            await asyncio.gather(
                self.forward_box_to_cloud(box_reader, cloud_writer, box_writer),
                self.forward_cloud_to_box(cloud_reader, box_writer),
                return_exceptions=True
            )
        except Exception as e:
            logger.info(f"Connection ended: {e}")
        finally:
            cloud_writer.close()
            box_writer.close()
            self.log_stats(force=True)
            self.save_results()

    async def forward_box_to_cloud(self, box_reader, cloud_writer, box_writer):
        """BOX → Cloud. Detekuje IsNewSet, injektuje, sleduje ACK a Events."""
        buffer = b""

        while True:
            try:
                data = await asyncio.wait_for(box_reader.read(4096), timeout=300)
                if not data:
                    break

                buffer += data
                text = buffer.decode('utf-8', errors='replace')

                while '<Frame>' in text and '</Frame>' in text:
                    start = text.find('<Frame>')
                    end = text.find('</Frame>') + len('</Frame>')
                    frame = text[start:end]
                    text = text[end:]
                    buffer = text.encode('utf-8')

                    frame_type = self.detect_frame_type(frame)

                    # === LOGOVÁNÍ - jen důležité eventy ===
                    should_log = frame_type in ('IsNewSet', 'ACK', 'Events', 'Setting')

                    if should_log:
                        self.save_frame("box→cloud", frame, frame_type)
                    else:
                        # Tiše počítej statistiku
                        self.stats[frame_type] += 1
                        self.log_stats()

                    # === IsNewSet - ZACHYTÍME, nepošleme cloudu ===
                    if '<Result>IsNewSet</Result>' in frame:
                        step = self.state.current_step
                        if step and self.state.waiting_for_isnewset and not step.injected:
                            logger.info("")
                            logger.info(f"{'='*50}")
                            logger.info("🎯 IsNewSet detekován")
                            logger.info(f"💉 INJEKTUJI: {step.name}")
                            logger.info(f"   {step.description}")
                            logger.info(f"{'='*50}")

                            step.started_at = datetime.now()
                            step.injected = True
                            self.state.waiting_for_isnewset = False

                            # Pošli Setting frame BOXu
                            box_writer.write(step.frame.encode())
                            await box_writer.drain()
                            self.save_frame("INJECT→box", step.frame, "Setting")

                            # NEPŘEPOSÍLEJ IsNewSet na cloud!
                            continue

                    # === ACK od BOXu na náš Setting ===
                    if '<Result>ACK</Result>' in frame and 'Setting' in frame:
                        step = self.state.current_step
                        if step and step.injected and not step.ack_received:
                            step.ack_received = True
                            logger.info("")
                            logger.info(f"{'='*50}")
                            logger.info(f"✅ ACK PŘIJAT: {step.name}")
                            logger.info(f"{'='*50}")
                        # ACK normálně forwardujeme na cloud (i když cloud o Setting neví)

                    # === tbl_events s MODE změnou ===
                    if 'tbl_events' in frame:
                        # Loguj všechny events
                        logger.info(f"📋 Events frame: {frame[:200]}...")

                        step = self.state.current_step
                        if step and step.injected and not step.event_received:
                            match = re.search(r'<Content>([^<]+)</Content>', frame)
                            if match:
                                content = match.group(1)
                                if 'MODE' in content:
                                    step.event_received = True
                                    step.event_content = content
                                    step.completed_at = datetime.now()

                                    logger.info("")
                                    logger.info(f"{'='*50}")
                                    logger.info(f"✅ EVENT PŘIJAT: {step.name}")
                                    logger.info(f"   {content}")
                                    logger.info(f"{'='*50}")

                                    # Naplánuj další krok
                                    asyncio.create_task(
                                        self.schedule_next_step(step.pause_after)
                                    )

                    # Forward na cloud (pokud jsme nedali continue výše)
                    cloud_writer.write((frame + '\r\n').encode())
                    await cloud_writer.drain()

            except asyncio.TimeoutError:
                logger.warning("⏰ Timeout BOX read")
                break
            except Exception as e:
                logger.error(f"Error box→cloud: {e}")
                break

    async def forward_cloud_to_box(self, cloud_reader, box_writer):
        """Cloud → BOX. Forwarduje, loguje jen důležité."""
        buffer = b""

        while True:
            try:
                data = await asyncio.wait_for(cloud_reader.read(4096), timeout=300)
                if not data:
                    break

                buffer += data
                text = buffer.decode('utf-8', errors='replace')

                while '<Frame>' in text and '</Frame>' in text:
                    start = text.find('<Frame>')
                    end = text.find('</Frame>') + len('</Frame>')
                    frame = text[start:end]
                    text = text[end:]
                    buffer = text.encode('utf-8')

                    frame_type = self.detect_frame_type(frame)

                    # Loguj jen důležité (Setting, END, Events)
                    should_log = frame_type in ('Setting', 'END', 'Events')
                    if should_log:
                        self.save_frame("cloud→box", frame, frame_type)
                        logger.info(f"☁️→📦 {frame_type}: {frame[:150]}...")

                    box_writer.write((frame + '\r\n').encode())
                    await box_writer.drain()

            except asyncio.TimeoutError:
                break
            except Exception as e:
                logger.error(f"Error cloud→box: {e}")
                break

    async def schedule_next_step(self, pause_seconds: int):
        logger.info(f"⏳ Pauza {pause_seconds}s...")
        await asyncio.sleep(pause_seconds)

        self.state.advance_step()

        if self.state.test_completed:
            logger.info("")
            logger.info(f"{'='*50}")
            logger.info("🏁 VŠECHNY TESTY DOKONČENY!")
            logger.info(f"{'='*50}")
        else:
            step = self.state.current_step
            if step:
                logger.info("")
                logger.info(f"➡️ DALŠÍ: {step.name} - {step.description}")

    def detect_frame_type(self, frame: str) -> str:
        if '<Result>ACK</Result>' in frame:
            return "ACK"
        elif '<Result>END</Result>' in frame:
            return "END"
        elif 'IsNewSet' in frame:
            return "IsNewSet"
        elif 'tbl_events' in frame:
            return "Events"
        elif 'tbl_actual' in frame:
            return "Actual"
        elif 'tbl_box' in frame and 'prms' not in frame:
            return "Box"
        elif 'Reason>Setting' in frame:
            return "Setting"
        elif 'TblName' in frame:
            match = re.search(r'<TblName>([^<]+)</TblName>', frame)
            if match:
                tbl = match.group(1)
                # Zkrať název
                return tbl.replace('tbl_', '').replace('_prms', 'P')
        return "other"

    async def run(self):
        server = await asyncio.start_server(
            self.handle_box, '0.0.0.0', self.listen_port
        )

        logger.info(f"🚀 Server na portu {self.listen_port}")
        logger.info("⏱️ Běží dokud nedokončí všechny testy (Ctrl+C pro ukončení)")

        print("\n" + "="*60)
        print("📋 TESTOVACÍ SEKVENCE:")
        for i, step in enumerate(self.state.steps):
            print(f"  {i+1}. {step.name}: {step.description}")
        print("="*60)

        step = self.state.current_step
        if step:
            logger.info(f"➡️ Čekám na BOX, první test: {step.name}")

        try:
            async with server:
                # Běž dokud test neskončí
                while not self.state.test_completed:
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Test přerušen (Ctrl+C)")
        finally:
            server.close()
            self.save_results()


async def main():
    proxy = ModeSequenceTest(listen_port=5710)
    await proxy.run()


if __name__ == "__main__":
    print("="*60)
    print("🧪 MODE SEQUENCE TEST")
    print("   Testuje replay starých framů pro změnu MODE")
    print("   + test modifikace NewValue se zachováním CRC")
    print("="*60 + "\n")

    asyncio.run(main())
