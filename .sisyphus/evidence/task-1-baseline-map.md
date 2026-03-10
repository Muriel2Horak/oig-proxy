# Task 1: Baseline Contract + Invariants Map

**Created:** 2026-03-10
**Task:** Baseline contract + invariants map
**Category:** quick

---

## Overview

This document identifies the current transport path coupling points in the OIG Proxy codebase. It serves as the baseline for the proxy-thin-pass-through-twin-sidecar-refactor plan.

---

## Section 1: Transport Coupling Points

### 1.1 proxy.py - `_route_box_frame_by_mode` (lines 858-914)

**Purpose:** Routes frames based on current proxy mode (ONLINE/HYBRID/OFFLINE)

**Coupling Points:**
- Line 871-880: OFFLINE mode → calls `_handle_frame_local_offline`
- Line 882-891: HYBRID mode (cloud unavailable) → calls `_handle_frame_local_offline`
- Line 893-902: ONLINE/HYBRID (cloud available) → calls `self._cf.forward_frame()`

**Transport-Only Invariant:** The proxy MUST NOT modify the payload when forwarding frames to cloud. It acts as a transparent pass-through.

```python
# Key transport call site
cloud_reader, cloud_writer = await self._cf.forward_frame(
    frame_bytes=data,
    table_name=table_name,
    device_id=device_id,
    conn_id=conn_id,
    box_writer=box_writer,
    cloud_reader=cloud_reader,
    cloud_writer=cloud_writer,
    connect_timeout_s=cloud_connect_timeout_s,
)
```

---

### 1.2 proxy.py - `_handle_box_frame_iteration` (lines 947-1004)

**Purpose:** Main frame processing loop - parses frame, handles twin ACK, routes by mode

**Coupling Points:**
- Line 958-962: Calls `_process_box_frame_with_guard` (parses XML, extracts device_id/table_name)
- Line 979: Calls `_maybe_handle_twin_ack` (twin ACK handling)
- Line 987-992: Calls `_maybe_handle_local_control_poll` (local control routing)
- Line 994-1003: Calls `_route_box_frame_by_mode` (transport routing)

**Transport-Only Invariant:** Frame parsing and telemetry recording do NOT modify the frame bytes. The payload remains unchanged when forwarded.

---

### 1.3 cloud_forwarder.py - `forward_frame` (lines 470-559)

**Purpose:** Main entry point for forwarding frames to cloud (oigservis.cz)

**Coupling Points:**
- Line 482-490: Calls `ensure_connected` (TCP connection management)
- Line 504-510: Calls `send_frame` (sends frame to cloud, reads ACK)
- Line 523-528: Calls `forward_ack_to_box` (forwards cloud ACK back to BOX)

**Transport-Only Invariant:**
- `send_frame` (lines 367-391): Writes frame_bytes directly to cloud_writer without modification
- `forward_ack_to_box` (lines 425-464): Writes ack_data directly to box_writer without modification
- Only transport errors (connection failures, timeouts) may cause deviation from transparent forwarding

---

### 1.4 Transport-Only Invariants Summary

| Invariant | Description | Location |
|-----------|-------------|----------|
| TRANSPORT-1 | Frame bytes MUST NOT be modified during forwarding | `forward_frame`, `send_frame`, `forward_ack_to_box` |
| TRANSPORT-2 | Only transport errors may cause local ACK fallback | `handle_connection_failed`, `handle_timeout`, `handle_eof`, `handle_error` |
| TRANSPORT-3 | Cloud ACK MUST be forwarded to BOX unchanged | `forward_ack_to_box` |
| TRANSPORT-4 | Proxy mode determines routing but not payload transformation | `_route_box_frame_by_mode` |

---

## Section 2: Twin Coupling Points

### 2.1 Twin Activation Hooks

**Location:** proxy.py lines 154, 158, 217-238, 240-264

| Hook | Method | Purpose |
|------|--------|---------|
| On MQTT Connect | `_install_twin_mqtt_on_connect_hook` (line 217) | Publishes initial twin state when MQTT connects |
| On MQTT Message | `_install_twin_mqtt_activation_hook` (line 240) | Arms pending twin activation when MQTT message received in ONLINE mode |

**Call Sites:**
```python
# Line 154: Install on connect hook
self._install_twin_mqtt_on_connect_hook()

# Line 158: Install activation hook
self._install_twin_mqtt_activation_hook()
```

---

### 2.2 Twin Lifecycle Hooks

**Location:** proxy.py lines 541-548, 570-576, 579-589

| Event | Method | Purpose |
|-------|--------|---------|
| BOX Reconnect | `self._twin.on_reconnect(conn_id=conn_id)` (line 542) | Handles BOX reconnection |
| BOX Disconnect (pending activation) | Lines 570-576 | Clears pending activation on disconnect |
| BOX Disconnect | `self._twin.on_disconnect(OnDisconnectDTO(...))` (line 580) | Handles BOX disconnection, moves inflight to replay |

---

### 2.3 Twin Routing Hooks

**Location:** proxy.py lines 830-834, 1187-1252

| Method | Purpose |
|--------|---------|
| `_dispatch_local_control_via_twin` (line 1187) | Dispatches IsNewSet/IsNewWeather/IsNewFW polls via digital twin |
| `_maybe_handle_twin_ack` (line 1254) | Handles ACK/NACK for twin-routed commands |
| `_maybe_handle_twin_event` (line 1344) | Handles tbl_events for twin state machine |

---

### 2.4 Twin State Queries

**Location:** proxy.py lines 267-269, 280, 447-451, 847-852

| Query | Method | Purpose |
|-------|--------|---------|
| Queue length | `self._twin.get_queue_length()` | Check if twin has pending settings |
| Inflight state | `self._twin.get_inflight()` | Check if twin has in-flight transaction |
| Twin availability | `_is_twin_routing_available()` (line 1169) | Check if twin is enabled and operational |

---

### 2.5 Twin Coupling Points Summary

| Coupling Point | File | Line(s) | Type |
|----------------|------|---------|------|
| Twin initialization | proxy.py | 146-153 | Lifecycle |
| MQTT handler setup | proxy.py | 213-214 | Lifecycle |
| On connect hook | proxy.py | 217-238 | Lifecycle |
| On activation hook | proxy.py | 240-264 | Lifecycle |
| On reconnect | proxy.py | 541-548 | Lifecycle |
| On disconnect | proxy.py | 579-589 | Lifecycle |
| Queue check | proxy.py | 267-269 | State query |
| Inflight check | proxy.py | 280, 847 | State query |
| Poll dispatch | proxy.py | 1208 | Command |
| ACK handling | proxy.py | 1294 | Command |
| Event handling | proxy.py | 1388 | Command |

---

## Section 3: Telemetry Publish Call Sites

### 3.1 TelemetryCollector Usage in proxy.py

**Location:** proxy.py (various lines)

| Call Site | Method | Purpose |
|-----------|--------|---------|
| Line 81 | `_proxy._tc.record_log_entry(record)` | Record log entries |
| Line 388 | `self._tc.record_response(...)` | Record proxy-to-BOX response |
| Line 470 | `self._tc.record_box_session_end(...)` | Record BOX session end |
| Line 561 | `self._tc.record_box_session_end(...)` | Record BOX session end (disconnect) |
| Line 566 | `self._tc.fire_event(...)` | Fire error event |
| Line 743 | `self._tc.record_frame_direction("box_to_proxy")` | Record frame direction |
| Line 745 | `self._tc.record_signal_class(table_name)` | Record signal class |
| Line 747 | `self._tc.record_end_frame(sent=False)` | Record END frame |
| Line 761 | `self._tc.record_request(table_name, conn_id)` | Record request |
| Line 769 | `self._tc.record_tbl_event(...)` | Record table event |
| Line 1059 | `self._tc.record_cloud_session_end(...)` | Record cloud session end |
| Line 1107 | `self._tc.record_cloud_session_end(...)` | Record cloud session end (offline) |

---

### 3.2 TelemetryCollector Usage in cloud_forwarder.py

**Location:** cloud_forwarder.py (various lines)

| Call Site | Method | Purpose |
|-----------|--------|---------|
| Line 97 | `self._proxy._tc.record_cloud_session_end(reason)` | Record cloud session end |
| Line 128 | `self._proxy._tc.record_cloud_session_end(...)` | Record cloud session end |
| Line 184 | `self._proxy._tc.fire_event(...)` | Fire error event |
| Line 204 | `self._proxy._tc.record_timeout(conn_id)` | Record timeout |
| Line 225 | `self._proxy._tc.record_cloud_session_end("eof")` | Record EOF |
| Line 226 | `self._proxy._tc.fire_event(...)` | Fire error event |
| Line 228 | `self._proxy._tc.record_error_context(...)` | Record error context |
| Line 249 | `self._proxy._tc.record_timeout(conn_id)` | Record timeout |
| Line 265 | `self._proxy._tc.fire_event(...)` | Fire error event |
| Line 270 | `self._proxy._tc.record_error_context(...)` | Record error context |
| Line 312 | `self._proxy._tc.record_cloud_session_end("timeout")` | Record timeout |
| Line 315 | `self._proxy._tc.record_timeout(conn_id)` | Record timeout |
| Line 338 | `self._proxy._tc.record_cloud_session_end("cloud_error")` | Record error |
| Line 339 | `self._proxy._tc.record_error_context(...)` | Record error context |
| Line 360 | `self._proxy._tc.record_timeout(conn_id)` | Record timeout |
| Line 380 | `self._proxy._tc.record_frame_direction("proxy_to_box")` | Record direction |
| Line 382 | `self._proxy._tc.record_end_frame(sent=True)` | Record END |
| Line 447 | `self._proxy._tc.record_response(...)` | Record response |
| Line 450 | `self._proxy._tc.record_frame_direction("cloud_to_proxy")` | Record direction |
| Line 452 | `self._proxy._tc.record_signal_class(table_name)` | Record signal |
| Line 454 | `self._proxy._tc.record_end_frame(sent=True)` | Record END |

---

### 3.3 TelemetryCollector Usage in hybrid_mode.py

**Location:** hybrid_mode.py (various lines)

| Call Site | Method | Purpose |
|-----------|--------|---------|
| Line 134 | `self._proxy._tc.record_hybrid_state_end(...)` | Record hybrid state end |
| Line 143 | `self._proxy._tc.record_offline_event(...)` | Record offline event |
| Line 146 | `self._proxy._tc.fire_event(...)` | Fire event |
| Line 165 | `self._proxy._tc.record_hybrid_state_end(...)` | Record hybrid state end |
| Line 172 | `self._proxy._tc.fire_event(...)` | Fire event |

---

### 3.4 Telemetry Publish Summary

**Total call sites identified:** 38+

**Categories:**
- Frame direction recording: 4 sites
- Signal class recording: 3 sites
- END frame recording: 4 sites
- Session end recording: 6 sites
- Timeout recording: 5 sites
- Error event firing: 6 sites
- Error context recording: 4 sites
- Request/response recording: 4 sites
- Table event recording: 1 site
- Log entry recording: 1 site
- Hybrid state recording: 4 sites

---

## Section 4: MQTT Publisher Coupling Points

### 4.1 Data Publishing

**Location:** mqtt_publisher.py line 609-636

```python
async def publish_data(self, data: dict[str, Any]) -> bool:
    """Publikování dat do MQTT."""
    # Maps data, sends discovery, publishes to MQTT
```

**Call Site in proxy.py:** Line 773
```python
await self.mqtt_publisher.publish_data(parsed)
```

---

### 4.2 Availability Publishing

**Location:** mqtt_publisher.py lines 362-369

**Call Sites in proxy.py:**
- Line 311: `self.mqtt_publisher.publish_availability()`
- Line 720: `self.mqtt_publisher.publish_availability()`

---

## Verification

This document was created by analyzing the following files:
- `addon/oig-proxy/proxy.py` (1392 lines)
- `addon/oig-proxy/cloud_forwarder.py` (559 lines)
- `addon/oig-proxy/mqtt_publisher.py` (711 lines)
- `addon/oig-proxy/digital_twin.py` (1565+ lines)

All sections contain the required coupling point mappings for the proxy-thin-pass-through-twin-sidecar-refactor plan.