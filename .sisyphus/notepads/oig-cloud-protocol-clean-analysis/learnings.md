# Database Schema Discovery Learnings

## Task 1: DB Schema Discovery (2026-03-03) - CORRECTED

### Key Findings:
1. **OIG Proxy Addon Databases**: Successfully located and extracted schemas from the correct OIG Proxy addon databases:
   - `/data/payloads.db` (118MB) - Main frame capture database
   - `/data/mqtt_queue.db` (32KB) - MQTT message queue database
   - `/data/telemetry_buffer.db` (16KB) - Telemetry buffer database
   - `/data/capture.db` (0 bytes) - Empty capture database

2. **Database Location Strategy**: 
   - OIG Proxy databases are stored inside the Docker container at `/data/`
   - Located container `addon_d7b5d5b1_oig_proxy` running via `sudo docker ps`
   - Used `sudo docker cp` to extract database files from container to host for analysis

3. **Schema Structure**:
   - **frames table** (payloads.db): Core table for captured OIG communication frames
   - **queue table** (mqtt_queue.db): Persistent MQTT message queue for offline resilience
   - **messages table** (telemetry_buffer.db): Telemetry data buffer during processing

### Successful Approach:
- SSH access via `ssh ha` to HA container
- Docker container inspection: `sudo docker inspect addon_d7b5d5b1_oig_proxy`
- Database extraction: `sudo docker cp container:/data/file.db /tmp/file.db`
- Schema extraction: `sqlite3 /tmp/file.db '.schema'`

### Key Tables for OIG Protocol Analysis:
- **frames**: Main OIG frame capture with ts, device_id, table_name, raw, parsed, direction
- **queue**: MQTT message persistence for cloud communication resilience
- **messages**: Telemetry buffer with topic, payload, timestamp, retries

### Technical Notes:
- Databases are SQLite3 with proper indexing (timestamps on queue and messages)
- Frames database supports both raw XML and parsed JSON formats
- Queue system provides offline resilience for cloud connectivity
- Container uses Alpine Linux base (sqlite3 not installed in container)

### Container Information:
- **Container Name**: addon_d7b5d5b1_oig_proxy
- **Image**: d7b5d5b1/amd64-addon-oig_proxy:1.6.1
- **Data Mount**: /mnt/data/supervisor/addons/data/d7b5d5b1_oig_proxy:/data
- **Status**: Running (healthy)

### Database Access Pattern:
- Live databases accessible only within container
- Extract schemas via docker cp + sqlite3 on host
- No direct host access to container databases (security isolation)
## Task 6: Data Validation & Pairing (2026-03-03)

### Timestamp Handling
- Loki uses nanosecond timestamps (Unix epoch in nanoseconds)
- DB uses ISO 8601 format with timezone info
- Must normalize all timestamps to same format (naive datetime) for comparison
- Timezone-aware and timezone-naive datetimes cannot be compared directly

### Session ID Extraction
- Loki logs may contain session/connection IDs in various formats:
  - `conn_id=N`
  - `conn=N`
  - `session=N`
  - `[Session=N]`
  - `box_session=N`
- Regex patterns needed to extract these from log lines

### Pairing Strategy
- Primary pairing: Session ID + Timestamp proximity (within 2 seconds)
- DB `conn_id` field maps to Loki session identifiers
- Chronological sorting enables timeline analysis
- Unpaired entries are still valuable for complete picture

### Data Format Conversion
- JSON evidence files can be converted to expected formats (JSON dump, CSV dump)
- CSV export from DB frames requires selecting relevant fields
- Unified timeline combines both sources with metadata about pairing status

## Task 8: Message Timing & Round-Trip Analysis (2026-03-03)

### Timing Metrics (from `unified_timeline.json`)
- V datasetu je 50 DB párů `box_to_proxy -> cloud_to_proxy` (stejný `conn_id` + `table_name`).
- Celkové RTT (Box -> Proxy -> Cloud -> Proxy): avg 9.578 ms, median 6.887 ms, p95 20.58 ms, min 4.659 ms, max 32.052 ms.
- Nejvyšší p95 má `tbl_actual` (27.304 ms), pak `tbl_batt_prms` (21.917 ms).

### Setting-specific Findings
- V aktuálním `unified_timeline.json` není žádný záznam s `table_name=setting`.
- Nelze tedy empiricky spočítat per-attempt `setting` flow (cloud send -> box response), ACK chování ani idle-session `setting` RTT z tohoto konkrétního dumpu.
- Výstupní `task-8-settings.json` explicitně ukládá nulový počet pokusů (`attempts_total=0`) a prázdný seznam pokusů.

### Raw TCP vs Decoded XML Relationship
- Tato timeline obsahuje pouze metadata rámců (`direction`, `table_name`, `conn_id`, `length`), nikoliv raw payload a decoded XML.
- Bez polí typu `raw*`/`xml*`/`parsed*` nelze na tomto vstupu provést korelaci raw TCP rámec vs dekódované XML.

## Task 7: Protocol State Machine Analysis (2026-03-03)

### Dataset Reality Check
- Aktuální `unified_timeline.json` obsahuje 110 eventů (100 DB + 10 Loki), ne 92k eventů.
- Detekované sessions: `conn_id=1` (DB lifecycle) a `conn_id=207` (Loki open event).

### Detekované stavy a přechody
- Pozorované stavy: `INIT`, `AUTH_PENDING`, `ACTIVE`, `IDLE`.
- Pozorované přechody: `INIT->ACTIVE`, `ACTIVE->IDLE`, `IDLE->ACTIVE`, `INIT->AUTH_PENDING`.
- Nezachycené v tomto dumpu: `SETTING_IN_PROGRESS`, `TIMEOUT`, `TAKEOVER`, `RECONNECTING`, `CLOSED`.

### Keep-alive / Ping a anomálie
- V datech nejsou detekované žádné ping/keep-alive eventy (`events_total=0`).
- Nebyly nalezeny timeouty, takeover eventy ani přerušená spojení s pending requesty.

### Korelace flow (Loki open vs DB direction)
- `conn_id=207`: Loki obsahuje explicitní otevření (`BOX connected`), ale bez navazujících DB rámců.
- `conn_id=1`: DB obsahuje kompletní střídání `box_to_proxy` a `cloud_to_proxy`, ale bez Loki open/close logů.
- Skript počítá i s `proxy_to_cloud`/`proxy_to_box` směry, pokud se objeví v jiném dumpu.
