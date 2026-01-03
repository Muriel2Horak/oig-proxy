#!/usr/bin/env python3
"""
Vizualizace request-response protokolu BOX ↔ CLOUD
Demonstruje, proč musíme posílat ACK během offline módu
"""
# pylint: disable=missing-module-docstring,missing-function-docstring,too-many-statements

def print_protocol_flow():
    print("=" * 80)
    print("BOX ↔ CLOUD PROTOKOL - Request-Response Pattern")
    print("=" * 80)
    print()

    print("┌─────────────────────────────────────────────────────────────────────────────┐")
    print("│ NORMÁLNÍ PROVOZ (Cloud online)                                             │")
    print("└─────────────────────────────────────────────────────────────────────────────┘")
    print()

    timeline = [
        ("08:59:07.309", "BOX → CLOUD", "tbl_dc_in frame", "(500 bytes)"),
        ("08:59:07.320", "CLOUD → BOX", "ACK", "(11ms delay) ✅"),
        ("", "", "", ""),
        ("08:59:12.777", "BOX → CLOUD", "tbl_ac_in frame", "(500 bytes)"),
        ("08:59:12.786", "CLOUD → BOX", "ACK", "(9ms delay) ✅"),
        ("", "", "", ""),
        ("08:59:17.420", "BOX → CLOUD", "tbl_ac_out frame", "(500 bytes)"),
        ("08:59:17.430", "CLOUD → BOX", "ACK", "(10ms delay) ✅"),
    ]

    for ts, direction, msg, note in timeline:
        if ts:
            print(f"  {ts}  {direction:15s}  {msg:20s}  {note}")
        else:
            print()

    print()
    print("📊 Pozorování:")
    print("   • BOX posílá frame")
    print("   • ČEKÁ na ACK (neposílá další frame dokud nedostane ACK!)")
    print("   • Cloud odpovídá ACK během 8-15ms")
    print("   • BOX pokračuje dalším framem")
    print()

    print("=" * 80)
    print()

    print("┌─────────────────────────────────────────────────────────────────────────────┐")
    print("│ SCÉNÁŘ 1: Cloud offline, PROXY NEPOSÍLÁ ACK (současný stav)                │")
    print("└─────────────────────────────────────────────────────────────────────────────┘")
    print()

    scenario1 = [
        ("10:00:00.000", "BOX → PROXY", "tbl_actual frame", ""),
        ("10:00:00.001", "PROXY → CLOUD", "connect failed!", "❌ Cloud offline"),
        ("10:00:00.001", "PROXY", "closes BOX socket", "❌ Fatal!"),
        ("10:00:00.002", "BOX", "connection reset", "❌ Detects disconnect"),
        ("10:00:28.000", "BOX", "reconnect attempt #1", "🔄 Waiting..."),
        ("10:00:28.001", "BOX → PROXY", "new TCP SYN", ""),
        ("10:00:28.002", "PROXY → CLOUD", "connect failed!", "❌ Still offline"),
        ("10:00:28.002", "PROXY", "closes BOX socket", "❌ Again!"),
        ("10:00:56.000", "BOX", "reconnect attempt #2", "🔄 Waiting..."),
        ("10:00:56.001", "BOX → PROXY", "new TCP SYN", ""),
        ("", "...", "loop continues...", "⚠️ Data loss!"),
    ]

    for ts, actor, msg, note in scenario1:
        if ts:
            print(f"  {ts}  {actor:15s}  {msg:25s}  {note}")
        else:
            print(f"  {' '*12}{actor:15s}  {msg:25s}  {note}")

    print()
    print("❌ Problémy:")
    print("   • BOX socket se zavře při cloud failure")
    print("   • BOX musí reconnect (28-48s interval)")
    print("   • Data jsou ztracená během reconnect loop")
    print("   • MQTT nedostává žádná data")
    print()

    print("=" * 80)
    print()

    print("┌─────────────────────────────────────────────────────────────────────────────┐")
    print("│ SCÉNÁŘ 2: Cloud offline, PROXY POSÍLÁ ACK (fallback mode)                  │")
    print("└─────────────────────────────────────────────────────────────────────────────┘")
    print()

    scenario2 = [
        ("10:00:00.000", "BOX → PROXY", "tbl_actual frame", ""),
        ("10:00:00.001", "PROXY → CLOUD", "connect failed!", "⚠️ Cloud offline"),
        ("10:00:00.001", "PROXY", "OFFLINE MODE", "✅ Switch to local ACK"),
        ("10:00:00.002", "PROXY → BOX", "ACK (local)", "✅ BOX happy!"),
        ("10:00:00.003", "PROXY → MQTT", "publish data", "✅ Data safe!"),
        ("", "", "", ""),
        ("10:00:09.000", "BOX → PROXY", "tbl_actual frame", "(9s later)"),
        ("10:00:09.001", "PROXY → BOX", "ACK (local)", "✅ Still offline"),
        ("10:00:09.002", "PROXY → MQTT", "publish data", "✅ Continuous data!"),
        ("", "", "", ""),
        ("10:00:18.000", "BOX → PROXY", "tbl_actual frame", "(9s later)"),
        ("10:00:18.001", "PROXY → BOX", "ACK (local)", "✅ Still offline"),
        ("10:00:18.002", "PROXY → MQTT", "publish data", "✅ Continuous data!"),
        ("", "", "", ""),
        ("", "...", "connection持续 (hours!)", "✅ No reconnects!"),
    ]

    for ts, actor, msg, note in scenario2:
        if ts:
            print(f"  {ts}  {actor:15s}  {msg:25s}  {note}")
        else:
            print(f"  {' '*12}{actor:15s}  {msg:25s}  {note}")

    print()
    print("✅ Výhody:")
    print("   • BOX socket zůstává aktivní (nekonečně dlouho!)")
    print("   • BOX NEMUSÍ reconnect")
    print("   • Data jdou do MQTT průběžně")
    print("   • Žádná data loss v MQTT")
    print()

    print("=" * 80)
    print()

    print("┌─────────────────────────────────────────────────────────────────────────────┐")
    print("│ SCÉNÁŘ 3: Cloud recovery (s frontováním)                                   │")
    print("└─────────────────────────────────────────────────────────────────────────────┘")
    print()

    scenario3 = [
        ("10:00:00.000", "BOX → PROXY", "tbl_actual #1", ""),
        ("10:00:00.001", "PROXY → BOX", "ACK (local)", "⚠️ Cloud offline"),
        ("10:00:00.002", "PROXY → QUEUE", "store frame #1", "📦 Queued"),
        ("10:00:00.003", "PROXY → MQTT", "publish #1", "✅ MQTT updated"),
        ("", "", "", ""),
        ("10:00:09.000", "BOX → PROXY", "tbl_actual #2", ""),
        ("10:00:09.001", "PROXY → BOX", "ACK (local)", "⚠️ Still offline"),
        ("10:00:09.002", "PROXY → QUEUE", "store frame #2", "📦 Queued"),
        ("10:00:09.003", "PROXY → MQTT", "publish #2", "✅ MQTT updated"),
        ("", "", "", ""),
        ("10:05:00.000", "PROXY", "cloud probe success!", "🎉 Cloud is back!"),
        ("10:05:00.100", "PROXY → CLOUD", "replay frame #1", "📤 Sending queued"),
        ("10:05:00.110", "CLOUD → PROXY", "ACK", "✅ Cloud received #1"),
        ("10:05:00.200", "PROXY → CLOUD", "replay frame #2", "📤 Sending queued"),
        ("10:05:00.210", "CLOUD → PROXY", "ACK", "✅ Cloud received #2"),
        ("10:05:00.300", "PROXY", "queue empty", "✅ All replayed!"),
        ("10:05:00.301", "PROXY", "FORWARD MODE", "✅ Normal operation"),
        ("", "", "", ""),
        ("10:05:09.000", "BOX → PROXY", "tbl_actual #3", "(new data)"),
        ("10:05:09.001", "PROXY → CLOUD", "forward #3", "✅ Direct to cloud"),
        ("10:05:09.010", "CLOUD → PROXY", "ACK", ""),
        ("10:05:09.011", "PROXY → BOX", "ACK (forward)", "✅ Back to normal!"),
    ]

    for ts, actor, msg, note in scenario3:
        if ts:
            print(f"  {ts}  {actor:15s}  {msg:25s}  {note}")
        else:
            print(f"  {' '*12}{actor:15s}  {msg:25s}  {note}")

    print()
    print("🚀 Kompletní řešení:")
    print("   • Offline: Local ACK + MQTT + Queue")
    print("   • Recovery: Replay queue → Cloud")
    print("   • Online: Forward mode (normal)")
    print("   • Výsledek: Žádná data loss (ani MQTT, ani Cloud!)")
    print()

    print("=" * 80)
    print()

def print_ack_analysis():
    print("┌─────────────────────────────────────────────────────────────────────────────┐")
    print("│ KRITICKÁ OTÁZKA: Musíme posílat ACK během offline módu?                    │")
    print("└─────────────────────────────────────────────────────────────────────────────┘")
    print()

    print("ODPOVĚĎ: ANO! Absolutně! ✅✅✅")
    print()

    print("Důvody:")
    print()
    print("1️⃣  BOX ČEKÁ na ACK před odesláním dalšího frame")
    print("    ├─► Pokud nedostane ACK → timeout")
    print("    ├─► Timeout → BOX zavře spojení")
    print("    └─► Zavření → BOX musí reconnect (porod!)")
    print()

    print("2️⃣  ACK je POVINNÁ součást protokolu")
    print("    ├─► Není to 'optional'")
    print("    ├─► Je to request-response pattern")
    print("    └─► Každý frame MUSÍ dostat odpověď")
    print()

    print("3️⃣  Bez ACK = mrtvé spojení")
    print("    ├─► BOX pošle frame")
    print("    ├─► Čeká... čeká... čeká...")
    print("    ├─► Timeout (30s? 60s? neznáme přesně)")
    print("    └─► Disconnect → reconnect loop ❌")
    print()

    print("4️⃣  S ACK = šťastný BOX")
    print("    ├─► BOX pošle frame")
    print("    ├─► Dostane ACK během 10ms")
    print("    ├─► BOX je spokojený")
    print("    └─► Spojení trvá 57.8 hodin! ✅")
    print()

    print("=" * 80)
    print()

def print_queue_comparison():
    print("┌─────────────────────────────────────────────────────────────────────────────┐")
    print("│ POROVNÁNÍ: S frontováním vs Bez frontování                                 │")
    print("└─────────────────────────────────────────────────────────────────────────────┘")
    print()

    comparison = [
        ("Kritérium", "BEZ frontování", "S frontováním"),
        ("-" * 20, "-" * 25, "-" * 25),
        ("Data v MQTT", "✅ ANO", "✅ ANO"),
        ("Data v Cloud", "❌ Gap během outage", "✅ Všechna data"),
        ("Složitost", "✅ Jednoduchá", "⚠️ Střední"),
        ("Paměť", "✅ Minimální", "⚠️ ~1-5 MB"),
        ("Implementace", "✅ 2-3 hodiny", "⚠️ 3-4 hodiny"),
        ("Testing", "✅ 1 hodina", "⚠️ 2 hodiny"),
        ("Risk", "✅ Nízký", "⚠️ Edge cases"),
        ("Benefit", "⚠️ Částečný", "✅ Úplný"),
    ]

    for row in comparison:
        print(f"  {row[0]:25s} │ {row[1]:25s} │ {row[2]:25s}")

    print()
    print("📊 Doporučení:")
    print()
    print("   Fáze 1 (TEĎ): Implementuj BEZ frontování")
    print("   ├─► Rychlé (2-3 hodiny)")
    print("   ├─► Jednoduché (nízký risk)")
    print("   ├─► Okamžitý benefit (MQTT data během outage)")
    print("   └─► Cloud má gap (přijatelné, outage je rare)")
    print()
    print("   Fáze 2 (POZDĚJI): Přidej frontování")
    print("   ├─► Po týdnu testování fáze 1")
    print("   ├─► Když je jistota že offline mode funguje")
    print("   └─► Cloud dostane kompletní data (no gap)")
    print()

    print("=" * 80)
    print()

def print_implementation_example():
    print("┌─────────────────────────────────────────────────────────────────────────────┐")
    print("│ IMPLEMENTACE: Offline mode s ACK (bez frontování)                          │")
    print("└─────────────────────────────────────────────────────────────────────────────┘")
    print()

    code = '''
async def _run_offline_mode(self, conn_id, box_reader, box_writer):
    """
    Offline mode - BOX stays connected, proxy sends local ACK
    NO queueing yet (phase 1)
    """

    logger.info(f"[#{conn_id}] OFFLINE MODE activated")

    while True:
        # Read frame from BOX
        data = await asyncio.wait_for(
            box_reader.read(8192),
            timeout=120.0  # 2min timeout for zombie detection
        )

        if not data:  # EOF
            logger.info(f"[#{conn_id}] BOX closed connection")
            break

        # Parse frame
        frame = data.decode('utf-8', errors='ignore')
        table_name = self._extract_table_name(frame)

        # CRITICAL: Send ACK IMMEDIATELY!
        ack = self._generate_ack(table_name)
        box_writer.write(ack.encode('utf-8'))
        await box_writer.drain()

        # THEN process (MQTT only, no queue yet)
        await self._publish_to_mqtt(frame, table_name)

        # Log
        logger.debug(f"[#{conn_id}] {table_name} → ACK → MQTT ✅")


def _generate_ack(self, table_name):
    """Generate appropriate ACK response"""
    if table_name == 'IsNewSet':
        # For IsNewSet queries, send END
        return '<Frame><Result>END</Result><CRC>34500</CRC></Frame>'
    else:
        # For data frames, send ACK
        return '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>'
    '''

    print(code)
    print()
    print("🎯 Klíčové body:")
    print("   1. ✅ ACK se posílá OKAMŽITĚ (před MQTT)")
    print("   2. ✅ BOX dostane odpověď během ~1ms")
    print("   3. ✅ Pak teprve MQTT (async, neblokuje)")
    print("   4. ✅ Žádné frontování (fáze 1)")
    print("   5. ✅ Spojení drží neomezeně (timeout jen pro zombie)")
    print()

    print("=" * 80)
    print()

if __name__ == '__main__':
    print()
    print_protocol_flow()
    print()
    print_ack_analysis()
    print()
    print_queue_comparison()
    print()
    print_implementation_example()

    print("┌─────────────────────────────────────────────────────────────────────────────┐")
    print("│ ZÁVĚR                                                                       │")
    print("└─────────────────────────────────────────────────────────────────────────────┘")
    print()
    print("✅ ANO, musíme posílat ACK během offline módu")
    print("✅ ANO, BOX čeká na ACK před dalším framem")
    print("✅ ANO, bez ACK se BOX zasekne a disconnectne")
    print()
    print("📦 Frontování: Nice to have, ale ne nutné v první fázi")
    print("   ├─► Fáze 1: ACK + MQTT (bez queue)")
    print("   └─► Fáze 2: ACK + MQTT + Queue + Replay")
    print()
    print("🚀 Doporučení: Implementuj fázi 1 TEĎ, fázi 2 POZDĚJI")
    print()
    print("=" * 80)
    print()
