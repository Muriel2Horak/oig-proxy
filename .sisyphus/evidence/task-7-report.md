# Task 7 - Protocol State Machine Analysis

- Input file: `unified_timeline.json`
- Normalized events: `110`
- Connections detected: `2`

## Detected States

- `ACTIVE`: 11
- `AUTH_PENDING`: 1
- `IDLE`: 10
- `INIT`: 2
- Not observed in this dataset: SETTING_IN_PROGRESS, TIMEOUT, TAKEOVER, RECONNECTING, CLOSED

## TCP Session Lifecycle

- conn `1` | start `2025-12-18T19:08:17.557413` | end `2025-12-18T19:17:33.520988` | duration `555.963575s` | final `ACTIVE` | frames `100`
- conn `207` | start `2026-03-03T11:43:05.662985` | end `2026-03-03T11:43:05.663820` | duration `0.000835s` | final `AUTH_PENDING` | frames `0`

## Keep-Alive / Ping Frequency

- events=0 | mean_interval_s=None | median_interval_s=None | p95_interval_s=None
- No ping/keep-alive events were present in this dataset.

## Anomalies

- interrupted_connections=0 | timeouts=0 | takeovers=0

## Flow Correlation (Loki conn open -> DB frame directions)

- conn `1` | loki_open=None | first_db={'timestamp': '2025-12-18T19:08:17.557413', 'direction': 'box_to_proxy', 'table_name': 'tbl_batt_prms'}
- conn `207` | loki_open={'timestamp': '2026-03-03T11:43:05.663820', 'log_line': "2026-03-03 11:43:05 [INFO] proxy: 🔌 BOX connected (conn=207, peer=('10.0.0.166', 8467))"} | first_db=None

## Transition Matrix

- `ACTIVE->IDLE`: 10
- `IDLE->ACTIVE`: 10
- `INIT->ACTIVE`: 1
- `INIT->AUTH_PENDING`: 1
