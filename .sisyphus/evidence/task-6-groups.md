# Task 6: Test Markers and Groups Documentation

## Overview
This document describes the test markers and groups created for the TDD harness update for refactor waves (Wave 2/3 extraction).

## Test Markers

### 1. `@pytest.mark.transport`
**Purpose**: Tests for transport layer extraction (Wave 2)

**Test File**: `tests/test_transport_only.py`

**Test Classes**:
- `TestTransportLayerExtraction` - Core transport abstraction tests
- `TestTransportErrorHandling` - Error handling and reconnection
- `TestTransportMetrics` - Metrics collection

**Run Command**:
```bash
pytest tests/test_transport_only.py -v -m transport
```

**Expected Implementation**:
- `transport.TransportFactory` class
- `transport.TransportType` enum (TCP, UDP)
- Transport abstraction with connect/send/receive/close methods
- Metrics tracking (bytes_sent, bytes_received)
- Reconnection logic

---

### 2. `@pytest.mark.telemetry`
**Purpose**: Tests for telemetry TAP extraction (Wave 2)

**Test File**: `tests/test_telemetry_tap.py`

**Test Classes**:
- `TestTelemetryTapExtraction` - Core TAP abstraction tests
- `TestTelemetryTapFiltering` - Metric/event filtering
- `TestTelemetryTapExport` - Export to Prometheus/JSON
- `TestTelemetryTapIntegration` - Proxy integration

**Run Command**:
```bash
pytest tests/test_telemetry_tap.py -v -m telemetry
```

**Expected Implementation**:
- `telemetry_tap.TelemetryTapFactory` class
- `telemetry_tap.TapType` enum (METRICS, EVENTS, TRACES)
- TAP abstraction with record/get/export methods
- Filtering by name pattern and labels
- Export to Prometheus and JSON formats
- Auto-attachment to proxy for event collection

---

### 3. `@pytest.mark.twin_activation`
**Purpose**: Tests for digital twin sidecar extraction (Wave 3)

**Test File**: `tests/test_twin_sidecar.py`

**Test Classes**:
- `TestTwinSidecarExtraction` - Core sidecar abstraction tests
- `TestTwinSidecarCommunication` - IPC messaging
- `TestTwinSidecarActivation` - Activation guards
- `TestTwinSidecarHealth` - Health monitoring

**Run Command**:
```bash
pytest tests/test_twin_sidecar.py -v -m twin_activation
```

**Expected Implementation**:
- `twin_sidecar.TwinSidecarFactory` class
- Sidecar process management (start/stop/restart)
- IPC messaging (send/receive)
- Message queue management
- Activation guards (prerequisites check)
- Health monitoring and auto-restart

---

## Running All New Tests

### Run all new marker tests:
```bash
pytest tests/test_transport_only.py tests/test_telemetry_tap.py tests/test_twin_sidecar.py -v
```

### Run by marker:
```bash
pytest -m transport -v
pytest -m telemetry -v
pytest -m twin_activation -v
```

### Run all markers together:
```bash
pytest -m "transport or telemetry or twin_activation" -v
```

---

## Test Structure

Each test file follows the TDD RED-GREEN pattern:

1. **RED Phase**: Tests fail because modules don't exist yet
2. **Implementation**: Create the modules to make tests pass
3. **GREEN Phase**: All tests pass

---

## Verification

### Check markers are registered:
```bash
pytest --markers | grep -E "(transport|telemetry|twin_activation)"
```

### Run with verbose marker output:
```bash
pytest tests/test_transport_only.py tests/test_telemetry_tap.py tests/test_twin_sidecar.py -v -m "transport or telemetry or twin_activation" --tb=short
```

---

## Test Count Summary

| Marker | Test File | Test Classes | Test Methods |
|--------|-----------|--------------|--------------|
| transport | test_transport_only.py | 3 | 10 |
| telemetry | test_telemetry_tap.py | 4 | 10 |
| twin_activation | test_twin_sidecar.py | 4 | 11 |
| **TOTAL** | **3** | **11** | **31** |