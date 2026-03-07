# Data Extraction - DB Export Findings

## Task Completed: Export SQLite DB for yesterday's data

### Database Information
- **Database Path**: `/data/payloads.db` in container `addon_d7b5d5b1_oig_proxy`
- **Table Name**: `frames`
- **Timestamp Format**: ISO 8601 (e.g., `2026-03-03T10:45:13.237387+00:00`)
- **Data Range**: 2026-03-02 00:00:00 to 2026-03-02 23:59:59 (UTC)

### Export Results
- **Total Records Exported**: 49,870 (49,869 data rows + 1 header)
- **Output File**: `db_dump.csv` (28.2 MB)
- **Evidence File**: `.sisyphus/evidence/task-5-db-count.txt`

### Table Schema
```sql
CREATE TABLE frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    device_id TEXT,
    table_name TEXT,
    raw TEXT,
    raw_b64 TEXT,
    parsed TEXT,
    direction TEXT,
    conn_id INTEGER,
    peer TEXT,
    length INTEGER
);
```

### Export Method Used
1. Copied database from container to `/tmp/payloads.db` on `ha`
2. Transferred database to local `/tmp/payloads.db` via ssh
3. Executed SQLite query to filter for yesterday's data
4. Exported to CSV with headers
5. Cleaned up temporary files

### Key Learnings
- Timestamps are stored in ISO 8601 format with UTC timezone
- Database contains both raw XML and JSON parsed data
- Large database size (~118MB) indicates significant data volume
- Efficient to filter by timestamp directly in SQLite query

### Next Steps
- The CSV file is ready for analysis
- Consider compression for storage if needed
- Database connection method established for future exports