# MQTT Telemetry Contract - Task 5

## Overview

This document defines the MQTT telemetry contract that must remain unchanged for the OIG Proxy system. The contract governs how telemetry data from OIG devices is published to MQTT topics.

## Contract Snapshot

**Document Version:** 1.0
**Created:** 2026-03-10
**Source Files Analyzed:**
- `addon/oig-proxy/mqtt_publisher.py` (lines 1-711)
- `addon/oig-proxy/proxy.py` (lines 1-1392)
- `addon/oig-proxy/config.py`

---

## 1. Topic Patterns

### 1.1 Table State Topics (tbl_*)

**Pattern:**
```
oig_local/<device_id>/<tbl_name>/state
```

**Examples:**
- `oig_local/12345/tbl_actual/state`
- `oig_local/12345/tbl_batt_prms/state`
- `oig_local/12345/tbl_box_prms/state`
- `oig_local/12345/tbl_inverter/state`

**Source Code Reference:**
- File: `mqtt_publisher.py`
- Method: `_state_topic()` (lines 383-387)
- Implementation:
  ```python
  @staticmethod
  def _state_topic(dev_id: str, table: str | None) -> str:
      """Vrátí state topic pro tabulku."""
      if table:
          return f"{MQTT_NAMESPACE}/{dev_id}/{table}/state"
      return f"{MQTT_NAMESPACE}/{dev_id}/state"
  ```

### 1.2 Events Topic (tbl_events)

**Pattern:**
```
oig_local/<device_id>/tbl_events/state
```

**Examples:**
- `oig_local/oig_proxy/tbl_events/state` (proxy device)
- `oig_local/12345/tbl_events/state` (specific device)

**Source Code Reference:**
- File: `mqtt_publisher.py`
- Method: `publish_data()` (lines 609-636)
- Table determination: `_determine_target_device_id()` (lines 550-553)
  ```python
  def _determine_target_device_id(self, table: str | None) -> str:
      if table in ("proxy_status", "tbl_events"):
          return self.proxy_device_id
      return self.device_id
  ```

### 1.3 Proxy Status Topic

**Pattern:**
```
oig_local/<proxy_device_id>/proxy_status/state
```

**Default:**
```
oig_local/oig_proxy/proxy_status/state
```

**Source Code Reference:**
- File: `mqtt_publisher.py`
- Method: `publish_proxy_status()` (lines 706-711)
  ```python
  async def publish_proxy_status(self, status_payload: dict[str, Any]) -> bool:
      """Publikuje stav proxy jako samostatnou tabulku proxy_status."""
      data = {"_table": "proxy_status"}
      data.update(status_payload)
      return await self.publish_data(data)
  ```

### 1.4 Availability Topic

**Pattern:**
```
oig_local/<device_id>/availability
```

**Payload:**
- `online` (retain=true, qos=1)
- `offline` (retain=true, qos=1, will set on disconnect)

**Source Code Reference:**
- File: `mqtt_publisher.py`
- Method: `publish_availability()` (lines 362-369)
- Will set: `_create_client()` (lines 108-111)

---

## 2. Namespace Conventions

### 2.1 Namespace Prefix

**Default:** `oig_local`

**Environment Variable:** `MQTT_NAMESPACE`

**Source Code Reference:**
- File: `config.py` (line 86)
  ```python
  MQTT_NAMESPACE = os.getenv("MQTT_NAMESPACE", "oig_local")
  ```

### 2.2 Proxy Device ID

**Default:** `oig_proxy`

**Environment Variable:** `PROXY_DEVICE_ID`

**Source Code Reference:**
- File: `config.py` (line 378)
  ```python
  PROXY_DEVICE_ID = os.getenv("PROXY_DEVICE_ID", "oig_proxy")
  ```

---

## 3. Payload Structure

### 3.1 JSON Format

All payloads are JSON-encoded dictionaries.

**Example:**
```json
{
  "tbl_actual:GridPower": -1234,
  "tbl_actual:BattPower": 567,
  "tbl_actual:SolarPower": 890,
  "tbl_actual:Timestamp": "2026-03-10T10:30:00+01:00"
}
```

### 3.2 Key Naming Convention

**Pattern:** `<table_name>:<sensor_id>`

**Source Code Reference:**
- File: `mqtt_publisher.py`
- Method: `_json_key()` (lines 402-403)
  ```python
  @staticmethod
  def _json_key(sensor_id: str) -> str:
      return sensor_id.split(":", 1)[1] if ":" in sensor_id else sensor_id
  ```

### 3.3 Sensor Discovery

Sensors are automatically discovered via Home Assistant MQTT discovery:

**Topic Pattern:**
```
homeassistant/<component>/<unique_id>/config
```

**Source Code Reference:**
- File: `mqtt_publisher.py`
- Method: `send_discovery()` (lines 508-548)
- Build method: `_build_discovery_payload()` (lines 464-506)

---

## 4. Retained Messages

### 4.1 State Retention

**Default:** `true` (retained)

**Environment Variable:** `MQTT_STATE_RETAIN`

**Source Code Reference:**
- File: `config.py` (line 88)
  ```python
  MQTT_STATE_RETAIN = os.getenv("MQTT_STATE_RETAIN", "true").lower() == "true"
  ```

**Usage:**
- File: `mqtt_publisher.py`
- Method: `_execute_publish()` (line 586)
  ```python
  result = client.publish(
      topic, payload, qos=MQTT_PUBLISH_QOS, retain=MQTT_STATE_RETAIN
  )
  ```

### 4.2 Discovery Retention

All Home Assistant discovery messages are published with `retain=True`.

**Source Code Reference:**
- File: `mqtt_publisher.py`
- Method: `send_discovery()` (lines 535-537)
  ```python
  result = self.client.publish(
      topic, json.dumps(discovery_payload), retain=True, qos=1
  )
  ```

---

## 5. QoS Levels

### 5.1 Publish QoS

**Default:** 1

**Source Code Reference:**
- File: `config.py` - `MQTT_PUBLISH_QOS`
- Used in: `mqtt_publisher.py`, method `_execute_publish()` (line 586)

### 5.2 Discovery QoS

**Default:** 1

**Source Code Reference:**
- File: `mqtt_publisher.py`, method `send_discovery()` (line 536)

---

## 6. Publish Call Sites

### 6.1 Main Data Publish

**Location:** `proxy.py`, method `_process_box_frame_common()` (line 773)

```python
await self.mqtt_publisher.publish_data(parsed)
```

**Flow:**
1. Parse XML frame from OIG device
2. Extract device_id and table_name
3. Call `mqtt_publisher.publish_data(parsed)`
4. Publisher determines target device_id based on table
5. Publisher builds topic: `{namespace}/{target_device_id}/{table}/state`
6. Publisher sends MQTT message with JSON payload

### 6.2 Proxy Status Publish

**Location:** `proxy.py`, method `publish_proxy_status()` (lines 363-365)

```python
async def publish_proxy_status(self) -> None:
    """Delegate to ProxyStatusReporter."""
    await self._ps.publish()
```

---

## 7. Contract Constraints

### 7.1 MUST NOT CHANGE

1. **Topic pattern structure:** `oig_local/<device_id>/<table>/state`
2. **Namespace prefix:** `oig_local` (unless explicitly reconfigured)
3. **Payload JSON format:** Keys must be `<table>:<sensor_id>` format
4. **Retained message behavior:** State messages must be retained
5. **Device ID for events:** `tbl_events` must use `proxy_device_id` (default: `oig_proxy`)
6. **Device ID for proxy_status:** Must use `proxy_device_id` (default: `oig_proxy`)

### 7.2 MAY BE CONFIGURED

1. **MQTT_NAMESPACE:** Can be changed via environment variable
2. **PROXY_DEVICE_ID:** Can be changed via environment variable
3. **MQTT_STATE_RETAIN:** Can be toggled via environment variable
4. **MQTT_PUBLISH_QOS:** Can be configured via environment variable

---

## 8. Verification

### 8.1 Test Assertions

The contract can be verified by checking:

1. **Topic format:** Regex pattern `^oig_local/[^/]+/(tbl_[a-z_]+|proxy_status)/state$`
2. **Payload JSON:** Valid JSON with expected key format
3. **Retained flag:** Check MQTT message properties
4. **Device ID routing:** Verify `tbl_events` uses `oig_proxy` device ID

### 8.2 Evidence Files

- **Golden assertions:** `.sisyphus/evidence/task-5-topic-pass.txt`
- **Drift detection:** `.sisyphus/evidence/task-5-topic-error.txt`

---

## 9. Dependencies

### 9.1 Blocking Tasks

- T8: Digital twin MQTT integration
- T15: Mode transition telemetry
- T19: Final integration testing

### 9.2 Related Files

- `addon/oig-proxy/mqtt_publisher.py` - Core MQTT publishing logic
- `addon/oig-proxy/proxy.py` - Publish call sites
- `addon/oig-proxy/config.py` - Configuration defaults
- `addon/oig-proxy/sensor_map.json` - Sensor configuration

---

## 10. Change Log

| Version | Date | Description |
|---------|------|-------------|
| 1.0 | 2026-03-10 | Initial contract documentation |