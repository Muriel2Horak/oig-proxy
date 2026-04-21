# OIG Proxy v2: Architecture

## Overview

OIG Proxy v2 is an async Python application that sits between an OIG Box (a solar/battery inverter controller) and the OIG cloud service (`bridge.oigpower.cz`). It intercepts the TCP communication, parses XML frames, publishes sensor data to an MQTT broker (with Home Assistant auto-discovery), and supports configurable operation modes for cloud outages.

The codebase lives in `addon/oig-proxy/` and runs as a Home Assistant add-on.

---

## High-Level Data Flow

```
OIG Box ──TCP──> ProxyServer (port 5710)
                      │
├── forward ──────────────────> bridge.oigpower.cz (cloud)
                      │          <── ACK/responses ──
                      │
                      ├── parse XML frames
                      │
                      ├── FrameProcessor ──> MQTTClient ──> MQTT broker
                      │         │                                  │
                      │         └── sensor_map lookup              └──> Home Assistant
                      │
                      ├── ModeManager (ONLINE / HYBRID / OFFLINE)
                      │
                      ├── TwinControlHandler (device settings via MQTT)
                      │
                      └── TelemetryCollector (anonymous usage stats)
```

In **ONLINE** mode, the proxy transparently forwards all traffic. The Box gets real cloud ACKs. The proxy reads the stream in parallel to extract and publish data.

In **OFFLINE** mode (or when cloud is unreachable in HYBRID), the proxy generates local ACK responses from `local_ack.py` so the Box keeps sending data even without cloud connectivity.

---

## Components

### ProxyServer (`proxy/server.py`)

The TCP server. It listens on `proxy_port` for incoming Box connections. For each connection, it either opens a matching upstream connection to the cloud or handles the session locally.

Key responsibilities:
- Accept TCP connections from the OIG Box
- Open corresponding TCP connection to `cloud_host:cloud_port`
- Bidirectional pipe: `_pipe_box_to_cloud` and `_pipe_cloud_to_box`
- Extract complete XML frames from the byte stream buffer
- Call `_process_frame` for each frame (triggers MQTT publish)
- Detect cloud failures and switch mode via `ModeManager`
- Serve local ACK frames in offline sessions via `_pipe_box_offline`
- Integrate with `TwinDelivery` for delivering pending device settings

Concurrency model: each Box connection spawns two concurrent coroutines (one per direction) using `asyncio.gather`. Multiple simultaneous Box connections are tracked in `_active_connections`.

```python
# Startup sequence
proxy = ProxyServer(config=config, on_frame=on_frame)
await proxy.start()         # begins listening
await stop_event.wait()     # runs until signal
await proxy.stop()          # graceful shutdown
```

### MQTTClient (`mqtt/client.py`)

A `paho-mqtt` wrapper with async-friendly interface. It runs the paho loop in a background thread (`loop_start`) so `publish_*` calls don't block the event loop.

Key features:
- Auto-reconnect with health check loop (`health_check_loop`)
- HA MQTT discovery: sends `homeassistant/{component}/{device_id}_{table}_{key}/config` topics on first publish
- LWT (Last Will Testament) for availability
- Subscription management for Twin control topics
- Deduplication of discovery messages (tracks sent topics in `_discovery_sent`)

State topics follow the pattern:
```
{namespace}/{device_id}/{table}/state
```

Default namespace is `oig_local`, so a typical state topic looks like:
```
oig_local/ABC123/tbl_actual/state
```

### FrameProcessor (`sensor/processor.py`)

Translates raw parsed frame dictionaries into MQTT-publishable data. For each key in the frame:

1. Looks up metadata from `SensorMapLoader` (name, unit, device_class, etc.)
2. Sends HA discovery config for unknown sensors
3. Decodes warning bits if the sensor entry has `warnings_3f`
4. Adds `{key}_warnings` list to publish data if any warnings are active
5. Calls `MQTTClient.publish_state`

Keys starting with `_` are internal and skipped.

### SensorMapLoader (`sensor/loader.py`)

Loads and caches `sensor_map.json`. Provides `lookup(table, key)` returning the full metadata dict or `None` for unknown sensors. Unknown sensors are tracked for operator review.

### ModeManager (`proxy/mode.py`)

Pure state machine managing `ONLINE`/`OFFLINE` runtime mode. The *configured mode* (from `config.proxy_mode`) determines the behavior envelope:

| Configured | Startup runtime | On cloud failure | On cloud recovery |
|---|---|---|---|
| `online` | ONLINE | stays ONLINE (error logged) | n/a |
| `hybrid` | ONLINE | OFFLINE after N failures | ONLINE |
| `offline` | OFFLINE | stays OFFLINE | stays OFFLINE |

In HYBRID mode, `fail_count` is incremented on each cloud connection error. Once `hybrid_fail_threshold` is reached, `in_offline` flips to `True` and the runtime mode becomes `OFFLINE`. A retry is attempted after `hybrid_retry_interval` seconds.

See `proxy_modes.md` for full state transition details.

### Twin Control (`twin/`)

Four modules handle device twin (settings push to Box):

- `state.py` (`TwinQueue`, `TwinSetting`): in-memory queue of pending settings keyed by `(table, key)`. Newer settings overwrite older ones.
- `handler.py` (`TwinControlHandler`): subscribes to `oig/{device_id}/control/set` MQTT topic, parses JSON payloads, enqueues settings.
- `delivery.py` (`TwinDelivery`): on each `IsNewSet` frame from Box, delivers all pending settings as XML frames. Provides `acknowledge(table, key)` to remove delivered settings.
- `ack_parser.py`: parses `<Result>ACK</Result>` / `<Result>END</Result>` responses from Box, extracts `TblName` and `ToDo` fields to know which setting was acknowledged.

See `twin.md` for the full flow.

### TelemetryCollector (`telemetry/collector.py`)

Background service that collects anonymous operational metrics and sends them to the telemetry service at `telemetry.muriel-cz.cz`. It runs as a separate asyncio task at a configurable interval (default 300 seconds).

The collector tracks:
- Frame counts (box-to-proxy, cloud-to-proxy, proxy-to-box)
- Box and cloud session records with durations
- HYBRID state transitions
- Offline events
- `tbl_events` data
- NACK reasons
- Error context windows with log captures

Telemetry can be disabled via `telemetry_enabled: false` in config.

### DeviceIdManager (`device_id.py`)

Persists the OIG Box device ID to `/data/device_id.json` on first observation. Subsequent frames with a different device ID are rejected (logged and ignored). This prevents data contamination if the proxy is accidentally connected to a different Box.

### Config (`config.py`)

Loaded entirely from environment variables at startup. All parameters have hard-coded defaults. See `configuration.md` for the full parameter reference.

---

## Startup Sequence

```
main() 
  → Config()                          # load env vars
  → configure_logging()
  → run(config)
      → DeviceIdManager.load()        # restore persisted device ID
      → MQTTClient()                  # create, don't connect yet
      → ProxyServer(config, on_frame) # create, don't listen yet
      → mqtt.connect(device_id)       # blocks up to CONNECT_TIMEOUT
      → TelemetryCollector.init()     # if enabled
      → TelemetryCollector.loop()     # start as asyncio task
      → TwinControlHandler.start()    # subscribe to MQTT control topic
      → proxy.start()                 # begin accepting TCP connections
      → mqtt.health_check_loop()      # start as asyncio task
      → stop_event.wait()             # run until SIGTERM/SIGINT
      → graceful shutdown:
          health_task.cancel()
          telemetry_task.cancel()
          twin_handler.stop()
          proxy.stop()
          mqtt.disconnect()
```

---

## Async Patterns

The entire proxy is built on Python's `asyncio`. A few key patterns:

**Per-connection coroutines.** Each Box connection spawns two coroutines via `asyncio.gather(pipe_box_to_cloud, pipe_cloud_to_box)`. They share no state except the `StreamWriter` for the other direction, which is safe because asyncio is single-threaded.

**Thread boundary at MQTT.** `paho-mqtt` runs its network loop in a thread. The proxy calls `mqtt.publish_state()` (non-blocking, paho queues it) from coroutines without any `await`. The health check loop is an asyncio coroutine that periodically calls blocking paho methods via `run_in_executor`.

**Signal handling.** `SIGTERM` and `SIGINT` both set a `stop_event`. The main coroutine awaits this event, then performs an ordered shutdown.

**Frame extraction.** The byte buffer accumulates raw bytes from the Box. `extract_frame_from_buffer` scans for complete framed messages and removes them from the buffer. This handles TCP segmentation transparently.

---

## Directory Structure

```
addon/oig-proxy/
├── main.py                  # Entry point, wires components
├── config.py                # Config loaded from env vars
├── device_id.py             # Device ID persistence
├── logging_config.py        # Logging setup
├── sensor_map.json          # Sensor metadata map
├── mqtt/
│   ├── client.py            # MQTT client (paho wrapper)
│   └── status.py            # ProxyStatusPublisher
├── protocol/
│   ├── frame.py             # Frame extraction from byte stream
│   ├── frames.py            # Frame builders (ACK, END, etc.)
│   ├── parser.py            # XML frame parser
│   └── crc.py               # CRC calculation
├── proxy/
│   ├── server.py            # TCP proxy server
│   ├── mode.py              # ModeManager (ONLINE/HYBRID/OFFLINE)
│   └── local_ack.py         # Local ACK builder for offline mode
├── sensor/
│   ├── loader.py            # SensorMapLoader
│   ├── processor.py         # FrameProcessor
│   └── warnings.py          # Warning bit decoder
├── telemetry/
│   ├── collector.py         # TelemetryCollector
│   └── client.py            # TelemetryClient (MQTT publisher)
└── twin/
    ├── state.py             # TwinQueue, TwinSetting
    ├── handler.py           # TwinControlHandler (MQTT subscriber)
    ├── delivery.py          # TwinDelivery (send settings to Box)
    └── ack_parser.py        # Parse Box ACK responses
```

---

## Home Assistant Devices

The proxy creates two "virtual devices" in HA:

**OIG Proxy** (`oig_proxy`): fixed device_id, always present. Contains:
- `proxy_status:*` - connection state, counters, mode, IsNewSet telemetry
- `tbl_events:*` - Type, Confirm, Content from Box events

**OIG Device** (`{device_id}`): auto-detected from first Box frame. Groups into sub-devices by `device_mapping` in sensor_map: `inverter`, `battery`, `grid`, `pv`, `load`, etc.

Availability is published via LWT to `{namespace}/{device_id}/availability` as `online` / `offline`.

---

## Protocol Overview

The OIG Box communicates using length-framed XML messages over TCP. Each frame has:
- 2-byte big-endian length prefix
- XML payload (inner content, not a full document)
- 2-byte CRC

The proxy uses `extract_frame_from_buffer` to find complete frames in the byte stream and `parse_xml_frame` to parse the inner XML into a Python dict. Key fields extracted by the parser:

- `_table` from `<TblName>`
- `_device_id` from `<ID_Device>`
- `_dt` from `<DT>`
- All other non-skipped tags as int, float, or str

Frames with `<ID_SubD>` > 0 are silently dropped (inactive battery banks, see SubD architecture in README).
