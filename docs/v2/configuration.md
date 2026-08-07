# OIG Proxy v2.2 Configuration Reference

Configuration is sampled once at add-on startup. Save changes and restart the add-on to apply them. The Home Assistant add-on exposes exactly 30 add-on parameters; `addon/oig-proxy/config.json` is the authoritative UI schema and `addon/oig-proxy/run` maps it to environment variables.

## Parameter Table

| Parameter | Environment | Default | Purpose |
|---|---|---:|---|
| `target_server` | `TARGET_SERVER` | `bridge.oigpower.cz` | Cloud hostname |
| `target_port` | `TARGET_PORT` | `5710` | Cloud TCP port |
| `proxy_port` | `PROXY_PORT` | `5710` | BOX-facing TCP port |
| `proxy_mode` | `PROXY_MODE` | `online` | `online`, `hybrid`, or `offline` |
| `cloud_ack_timeout` | `CLOUD_ACK_TIMEOUT` | `1800` | Legacy add-on timeout input; see precedence below |
| `local_getactual_enabled` | `LOCAL_GETACTUAL_ENABLED` | `false` | Opt-in local `GetActual` trigger |
| `local_getactual_interval_s` | `LOCAL_GETACTUAL_INTERVAL_S` | `10` | Trigger interval; clamped to at least 10 seconds |
| `hybrid_retry_interval` | `HYBRID_RETRY_INTERVAL` | `60` | Cloud retry interval |
| `hybrid_fail_threshold` | `HYBRID_FAIL_THRESHOLD` | `1` | Failures before HYBRID degrades |
| `mqtt_host` | `MQTT_HOST` | `core-mosquitto` | MQTT broker host |
| `mqtt_port` | `MQTT_PORT` | `1883` | MQTT broker port |
| `mqtt_username` | `MQTT_USERNAME` | empty | MQTT username |
| `mqtt_password` | `MQTT_PASSWORD` | empty | MQTT password |
| `ha_ip` | `HA_IP` | empty | HA address used by DNS override |
| `dns_override_ip` | `DNS_OVERRIDE_IP` | empty | Explicit DNS override destination |
| `dns_upstream` | `DNS_UPSTREAM` | `8.8.8.8` | Upstream DNS server |
| `log_level` | `LOG_LEVEL` | `INFO` | `INFO`, `DEBUG`, or `TRACE` |
| `proxy_status_interval` | `PROXY_STATUS_INTERVAL` | `60` | Proxy status publish interval |
| `full_refresh_interval_hours` | `FULL_REFRESH_INTERVAL_HOURS` | `24` | HA discovery refresh interval |
| `capture_payloads` | `CAPTURE_PAYLOADS` | `false` | Store parsed frame captures |
| `capture_raw_bytes` | `CAPTURE_RAW_BYTES` | `false` | Store raw base64 frame bytes |
| `capture_retention_days` | `CAPTURE_RETENTION_DAYS` | `7` | Capture retention |
| `capture_pcap` | `CAPTURE_PCAP` | `false` | Enable bounded PCAP capture |
| `control_mqtt_enabled` | `CONTROL_MQTT_ENABLED` | `false` | Enable local-setting ingress |
| `control_ack_timeout_s` | `CONTROL_ACK_TIMEOUT_S` | `30` | Delivery ACK/NACK deadline |
| `control_event_timeout_s` | `CONTROL_EVENT_TIMEOUT_S` | `300` | Exact execution-event deadline |
| `control_command_ttl_s` | `CONTROL_COMMAND_TTL_S` | `900` | Pending command lifetime |
| `control_max_attempts` | `CONTROL_MAX_ATTEMPTS` | `8` | Hard retry ceiling, clamped to 1–8 |
| `telemetry_enabled` | `TELEMETRY_ENABLED` | `true` | Anonymous operational telemetry |
| `max_concurrent_connections` | `MAX_CONCURRENT_CONNECTIONS` | `5` | Concurrent BOX connection limit |

The parameter order above intentionally matches `config.json` so documentation tests detect additions, removals, or reordered UI options.

## Local-control gate and restart behavior

`control_mqtt_enabled` is false by default and accepts only exact `true`/`false` or `1`/`0` environment values. Invalid input fails closed. On every start, including a disabled restart, the runtime opens and validates `/data/twin_queue.db` and performs recovery before it can start a control handler. With the gate disabled it creates no control subscription and performs no local write, but it preserves the database and its terminal/nonterminal history.

Changing the gate or any deadline requires an add-on restart. Runtime MQTT messages cannot enable the feature.

## Deadline precedence and compatibility

For the 2.2.x compatibility window, `CONTROL_ACK_TIMEOUT_S has precedence` whenever it is present. If it is absent, `CLOUD_ACK_TIMEOUT` is accepted as a deprecated local-control ACK timeout and emits a startup warning. If neither exists, the local-control default is 30 seconds. Values must be finite and are clamped to at least one second.

`CONTROL_EVENT_TIMEOUT_S` is the only execution-event deadline; there is no event-timeout alias. `CONTROL_COMMAND_TTL_S` likewise has no legacy alias. `CONTROL_MAX_ATTEMPTS` is parsed as base-10 and clamped to the shipped hard range 1–8.

The add-on UI still defaults `cloud_ack_timeout` to 1800 seconds for legacy installations. The dedicated `control_ack_timeout_s` value is authoritative for local transactions.

## Local-setting storage and failure behavior

`TWIN_DB_PATH` defaults to `/data/twin_queue.db`. It is an internal environment-only path, not an add-on UI field. Startup validates SQLite schema version, integrity, pragmas, transaction accounting, and recovery. A missing or corrupt store, a future schema, a lock or migration error, or inconsistent accounting disables local control instead of recreating or downgrading the database. Cloud forwarding remains transparent when the proxy has not taken ownership of a local dialogue.

`CLOUD_DIALOG_TIMEOUT_S` defaults to 30 seconds and bounds a correlated cloud-first ONLINE/HYBRID `IsNewSet` dialogue. It is separate from the local ACK and execution-event deadlines.

## Modes

- `online`: open the cloud dialogue first. Eligible local work may replace only its correlated cloud `END`; otherwise every cloud byte is forwarded unchanged.
- `hybrid`: identical cloud-first behavior while online; after the configured failure threshold it uses the offline response contract until the retry window opens.
- `offline`: never opens the cloud connection. Every complete BOX request gets exactly one local response; `IsNewSet` receives one Setting when eligible, otherwise `END`.

See [proxy_modes.md](proxy_modes.md) and [twin.md](twin.md) for ownership and correlation details.

## DNS and network settings

DNS override destination precedence is `dns_override_ip`, then `ha_ip`, then auto-detection. `target_server`, DNS values, and the upstream resolver are assembled at add-on start, so they also require a restart. `PROXY_HOST` defaults to `0.0.0.0` because the Home Assistant service must be reachable by the BOX LAN.

Additional internal environment defaults include:

| Environment | Default |
|---|---:|
| `TWIN_DB_PATH` | `/data/twin_queue.db` |
| `DEVICE_ID_PATH` | `/data/device_id.json` |
| `CLOUD_DIALOG_TIMEOUT_S` | `30` |
| `CLOUD_CONNECT_TIMEOUT` | `10` |
| `MQTT_NAMESPACE` | `oig_local` |
| `MQTT_QOS` | `1` |
| `MQTT_STATE_RETAIN` | `true` |
| `SENSOR_MAP_PATH` | `/data/sensor_map.json` |
| `CAPTURE_DB_PATH` | `/data/payloads.db` |
| `CAPTURE_PCAP_PATH` | `/data/capture.pcap` |

## Rollback

A 2.1.1 rollback does not understand schema v1 local-setting transactions. It ignores and preserves `/data/twin_queue.db`; do not delete the file during rollback. Returning to 2.2.0 reopens and validates it. Version 2.2.0 never downgrades or recreates a future or corrupt database.
