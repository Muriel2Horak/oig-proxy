# Database Schema Discovery Decisions

## Task 1: DB Schema Discovery (2026-03-03) - CORRECTED

### Key Decisions Made:
1. **Target Correction**: Abandoned Home Assistant main databases, focused on OIG Proxy addon
   - **Initial Error**: Extracted wrong databases (HA recorder dbs)
   - **Correction**: Located correct OIG Proxy addon databases
   - **Reason**: Plan specifically requires OIG Proxy databases, not HA system databases
   - **Outcome**: Found and extracted correct payloads.db, mqtt_queue.db, etc.

2. **Container Access Strategy**: Chose Docker-based access over filesystem search
   - **Reason**: OIG Proxy runs in Docker container with isolated filesystem
   - **Implementation**: Used `sudo docker ps` to find container, `sudo docker inspect` for details
   - **Outcome**: Successfully identified container `addon_d7b5d5b1_oig_proxy` and its data mount

3. **Database Extraction Method**: Chose Docker copy + host sqlite3 approach
   - **Reason**: Container lacks sqlite3 binary (Alpine Linux base)
   - **Implementation**: `sudo docker cp container:/data/file.db /tmp/file.db`
   - **Outcome**: Successfully extracted all database schemas without modifying live container

4. **Comprehensive Documentation**: Decision to document all OIG Proxy databases
   - **Included**: payloads.db, mqtt_queue.db, telemetry_buffer.db, capture.db
   - **Reason**: Complete picture of OIG Proxy data storage architecture
   - **Outcome**: All schemas documented with detailed field descriptions in evidence file

### Technical Strategy:
- **Safety-first**: No direct modification of live container databases
- **Non-disruptive**: Read-only access, copy databases outside container
- **Comprehensive**: Extract all databases from OIG Proxy addon
- **Accurate**: Focus on OIG Proxy specific databases, not general HA databases

### Container Information:
- **Target Container**: addon_d7b5d5b1_oig_proxy
- **Image**: d7b5d5b1/amd64-addon-oig_proxy:1.6.1
- **Data Location**: /data/ inside container
- **Access Method**: Docker copy to host for analysis

### Verification Approach:
- **Schema Validation**: Extracted and verified all table structures
- **File Verification**: Confirmed database files exist and contain data
- **Access Testing**: Successfully extracted schemas from copied files