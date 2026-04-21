# OIG Proxy v2: Configuration Reference

All configuration is loaded from environment variables at startup. In the Home Assistant add-on, these map to fields in the add-on **Configuration** tab.

The canonical defaults live in `addon/oig-proxy/config.py` (class attributes). The `config.json` schema defines the HA UI form, allowed types, and the option names.

---

## Parameter Table

| Parameter | Env var | Type | Default | Description |
|---|---|---|---|---|
| `target_server` | `TARGET_SERVER` | str | `bridge.oigpower.cz` | Cloud hostname the OIG Box normally connects to |
| `target_port` | `TARGET_PORT` | int | `5710` | Cloud TCP port |
| `proxy_port` | `PROXY_PORT` | int | `5710` | Local TCP port the proxy listens on for Box connections |
| `proxy_mode` | `PROXY_MODE` | enum | `online` | Operating mode: `online`, `hybrid`, or `offline` |
| `cloud_ack_timeout` | `CLOUD_ACK_TIMEOUT` | float | `30.0` | Seconds to wait for a cloud ACK before treating the request as failed |
| `hybrid_retry_interval` | `HYBRID_RETRY_INTERVAL` | int | `60` | Seconds between cloud reconnection attempts in HYBRID offline state |
| `hybrid_fail_threshold` | `HYBRID_FAIL_THRESHOLD` | int | `1` | Number of consecutive cloud failures before HYBRID switches to offline |
| `mqtt_host` | `MQTT_HOST` | str | `core-mosquitto` | MQTT broker hostname |
| `mqtt_port` | `MQTT_PORT` | int | `1883` | MQTT broker port |
| `mqtt_username` | `MQTT_USERNAME` | str | `` | MQTT username (empty = no auth) |
| `mqtt_password` | `MQTT_PASSWORD` | str | `` | MQTT password |
| `ha_ip` | `HA_IP` | str? | `` | HA LAN IP for dnsmasq override (empty = auto-detect) |
| `dns_override_ip` | `DNS_OVERRIDE_IP` | str? | `` | Explicit DNS target IP for the configured `target_server` override (takes priority over `ha_ip`) |
| `dns_upstream` | `DNS_UPSTREAM` | str? | `8.8.8.8` | Upstream DNS server for dnsmasq |
| `log_level` | `LOG_LEVEL` | enum | `INFO` | Log verbosity: `INFO`, `DEBUG`, or `TRACE` |
| `proxy_status_interval` | `PROXY_STATUS_INTERVAL` | int | `60` | Seconds between periodic proxy status MQTT publishes |
| `full_refresh_interval_hours` | `FULL_REFRESH_INTERVAL_HOURS` | int | `24` | Hours between forced HA discovery re-publish (0 = disabled) |
| `capture_payloads` | `CAPTURE_PAYLOADS` | bool? | `false` | Save all parsed frames to `/data/payloads.db` for debugging |
| `capture_raw_bytes` | `CAPTURE_RAW_BYTES` | bool? | `false` | Include raw base64-encoded bytes in the capture database |
| `capture_retention_days` | `CAPTURE_RETENTION_DAYS` | int? | `7` | Days to keep captured frames before pruning |
| `control_mqtt_enabled` | `CONTROL_MQTT_ENABLED` | bool? | `false` | Enable Twin control MQTT topic (device settings via `oig/{device_id}/control/set`) |
| `telemetry_enabled` | `TELEMETRY_ENABLED` | bool? | `true` | Enable anonymous operational telemetry |

**21 parameters total.**

---

## Detailed Descriptions

### `target_server` / `target_port`

The OIG cloud service the proxy forwards traffic to. Change these only if OIG changes their endpoint. Together with `proxy_port`, the proxy acts as a man-in-the-middle: the Box connects to the proxy port, and the proxy opens a separate TCP connection to `target_server:target_port`.

```
Box ──TCP:5710──> Proxy ──TCP:5710──> bridge.oigpower.cz
```

The DNS override makes the Box resolve the configured `target_server` to the HA host IP, so it connects to the proxy without any changes on the Box itself. When `target_server` is `bridge.oigpower.cz`, the add-on also keeps a legacy alias for `oigservis.cz` so older installs still land on the proxy.

Changes to `target_server`, `dns_override_ip`, `ha_ip`, or `dns_upstream` are applied on add-on startup, so save the config and restart the add-on after editing these fields.

### `proxy_port`

The TCP port the proxy listens on. Must match the port the Box connects to. Since the Box always connects to port 5710, this should not be changed unless you're running the proxy behind a port-mapping NAT.

The add-on exposes this via `host_network: true`, so the container shares the host's network namespace.

### `proxy_mode`

Controls cloud connectivity behavior. Three options:

- `online`: Always tries to forward to cloud. If the cloud is unavailable, logs an error and drops the Box connection.
- `hybrid`: Starts online. Falls back to local ACK generation after `hybrid_fail_threshold` consecutive failures. Retries cloud after `hybrid_retry_interval` seconds.
- `offline`: Never connects to cloud. All Box frames get local ACK responses.

See `proxy_modes.md` for full state machine details.

### `cloud_ack_timeout`

In seconds (float). How long the proxy waits for a response from the cloud for a given Box request. If the cloud doesn't respond in time, `ModeManager.record_failure()` is called.

Note: in `config.json` the default is 1800.0 (30 minutes), which is the conservative setting for the add-on. The Python `Config` class defaults to 30.0. If you're testing responsiveness of the HYBRID failover, set this lower.

### `hybrid_retry_interval`

How long (seconds) the proxy waits in the offline state before attempting a cloud reconnection. After the interval, the next Box connection attempt will try the cloud first. If it succeeds, `ModeManager.record_success()` flips back to ONLINE. If it fails, the offline window restarts.

### `hybrid_fail_threshold`

Number of consecutive cloud connection failures (or timeouts) before HYBRID enters offline state. Default is 1, meaning a single failure triggers offline mode. Raise this if you have a flaky connection and want more tolerance before going offline.

### `mqtt_host` / `mqtt_port`

The MQTT broker the proxy publishes sensor data to. In HA, the standard Mosquitto add-on is accessible at `core-mosquitto:1883` from other add-ons. If you're running the proxy outside HA, set these to your broker's address.

### `mqtt_username` / `mqtt_password`

Credentials for MQTT broker authentication. In HA, create a dedicated MQTT user in the Mosquitto add-on configuration. Leave both empty only if your broker allows anonymous access (not recommended in production).

### `ha_ip`

The LAN IP address of your Home Assistant instance. Used by the built-in dnsmasq server (port 53) to override the configured `target_server` DNS record. If left empty, the proxy attempts to auto-detect it. Set this explicitly if auto-detection picks the wrong interface.

Only relevant if you're using the proxy's DNS feature. If your router already handles the DNS override, leave this empty and don't worry about dnsmasq.

### `dns_override_ip`

Optional explicit override target for the built-in dnsmasq mapping (`address=/<target_server>/...`).

Priority order at startup:

1. `dns_override_ip` (if set)
2. `ha_ip` (if set)
3. auto-detected host IP

Use this when you want the Box to resolve the configured `target_server` to a non-HA endpoint (for example a NAS relay/sniffer IP) without changing `target_server` forwarding behavior.

### `dns_upstream`

Upstream DNS server for queries that aren't handled by the local override. Default is Google's `8.8.8.8`. Set this to your router's IP if you want LAN names to resolve correctly, or to another trusted resolver.

### `log_level`

Controls Python logging verbosity:

- `INFO`: normal operation messages, connections, mode changes, MQTT publishes
- `DEBUG`: frame content, sensor map lookups, MQTT discovery decisions
- `TRACE`: raw bytes, buffer state, very verbose per-frame output

Start with `INFO`. Switch to `DEBUG` when diagnosing specific issues.

### `proxy_status_interval`

The proxy publishes a `proxy_status` JSON payload to MQTT at this interval (seconds). This ensures HA shows current status even after a HA restart (retained topics restore on reconnect, but fresh data requires a new publish). Set to 0 to disable periodic publishing.

### `full_refresh_interval_hours`

How often to re-send all HA discovery messages (hours). Useful if HA restarts and loses entity configuration, though retained discovery topics normally handle this. Set to 0 to disable.

### `capture_payloads`

When enabled, every parsed frame is stored in `/data/payloads.db` (SQLite). Useful for debugging sensor map gaps or protocol analysis. Captures parsed data only (not raw bytes) unless `capture_raw_bytes` is also enabled.

### `capture_raw_bytes`

When enabled alongside `capture_payloads`, the raw base64-encoded frame bytes are included in the capture. Needed for low-level protocol analysis.

### `capture_retention_days`

Automatic pruning of capture database entries older than this many days. Prevents unbounded disk growth.

### `control_mqtt_enabled`

When enabled, the proxy subscribes to `oig/{device_id}/control/set` and accepts JSON settings payloads. These are queued and delivered to the Box on the next `IsNewSet` frame. See `twin.md` for the full flow.

### `telemetry_enabled`

When enabled, the proxy sends anonymous operational metrics to `telemetry.muriel-cz.cz` every 5 minutes. The data includes connection counts, frame rates, HYBRID state transitions, and error patterns. No personal data or sensor values are included.

Set to `false` if you don't want any outbound connections to external services beyond `bridge.oigpower.cz`.

---

## Minimal Working Configuration

For a typical Home Assistant add-on setup:

```yaml
target_server: bridge.oigpower.cz
target_port: 5710
proxy_port: 5710
proxy_mode: hybrid
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: oig_proxy
mqtt_password: your_password_here
log_level: INFO
hybrid_retry_interval: 60
hybrid_fail_threshold: 1
```

If using the proxy's built-in DNS:

```yaml
ha_ip: 192.168.1.100      # your HA LAN IP
dns_override_ip: ""       # optional; e.g. 10.0.0.160 for NAS relay
dns_upstream: 192.168.1.1  # your router
```

---

## Environment Variable Reference

For running outside the HA add-on:

```bash
export TARGET_SERVER=bridge.oigpower.cz
export TARGET_PORT=5710
export PROXY_PORT=5710
export PROXY_MODE=hybrid
export MQTT_HOST=localhost
export MQTT_PORT=1883
export MQTT_USERNAME=oig
export MQTT_PASSWORD=secret
export LOG_LEVEL=INFO
export HYBRID_RETRY_INTERVAL=60
export HYBRID_FAIL_THRESHOLD=1
export TELEMETRY_ENABLED=true
export PROXY_STATUS_INTERVAL=60
export SENSOR_MAP_PATH=/data/sensor_map.json
```

Additional env vars not exposed in the HA config UI:

| Env var | Default | Description |
|---|---|---|
| `PROXY_HOST` | `0.0.0.0` | Local bind address |
| `CLOUD_CONNECT_TIMEOUT` | `10.0` | TCP connect timeout to cloud (seconds) |
| `MQTT_NAMESPACE` | `oig_local` | MQTT topic prefix |
| `MQTT_QOS` | `1` | MQTT QoS level |
| `MQTT_STATE_RETAIN` | `true` | Whether state publishes use retain flag |
| `PROXY_DEVICE_ID` | `oig_proxy` | Fixed device ID for proxy status entities |
| `SENSOR_MAP_PATH` | `/data/sensor_map.json` | Path to sensor map file |
| `TELEMETRY_MQTT_BROKER` | `telemetry.muriel-cz.cz:1883` | Telemetry broker address |
| `TELEMETRY_INTERVAL_S` | `300` | Telemetry publish interval (seconds) |
