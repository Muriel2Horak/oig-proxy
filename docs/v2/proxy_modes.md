# OIG Proxy v2: Proxy Modes

The proxy supports three operating modes that control how it handles the relationship between the OIG Box and the OIG cloud service. The mode is configured via `proxy_mode` in `config.json` (env var `PROXY_MODE`).

Modes are managed by `ModeManager` in `proxy/mode.py`.

---

## Mode Overview

| Mode | Cloud connection | On cloud failure | Local ACK | Use when |
|---|---|---|---|---|
| `online` | Always required | Log error, drop Box connection | No | Cloud is always available |
| `hybrid` | Preferred, optional | Switch to offline after N failures | Yes (when offline) | Cloud may be unreliable |
| `offline` | Never attempted | n/a | Always | No cloud access |

---

## ONLINE Mode

The default. The proxy forwards all traffic transparently between the Box and cloud.

### Behavior

1. Box connects to proxy port
2. Proxy opens connection to `cloud_host:cloud_port`
3. All data flows: Box → Cloud and Cloud → Box
4. Proxy taps the Box-to-Cloud stream to parse and publish frames to MQTT
5. If cloud connection fails, proxy logs the error and closes the Box connection

The Box receives real ACK responses from the cloud. No local ACK generation happens.

### Flow Diagram

```
BOX ──connect──> ProxyServer
                    │
                    ├── open TCP to cloud ──> bridge.oigpower.cz
                    │
                    ├── _pipe_box_to_cloud:
                    │     read Box data
                    │     forward to cloud
                    │     parse frames -> on_frame() -> MQTT
                    │
                    └── _pipe_cloud_to_box:
                          read cloud responses
                          forward to Box
```

### Configuration

```yaml
proxy_mode: online
```

No additional parameters needed. `cloud_ack_timeout` and `hybrid_*` parameters are still read but have no effect.

### Failure Behavior

If the cloud TCP connection can't be established:
- `ModeManager.record_failure()` is called, but since the mode is `online`, `is_hybrid_mode()` returns `False`
- The failure is not counted toward any threshold
- The Box connection is closed immediately
- The Box will retry connecting (typically within seconds)

If cloud drops mid-session (TCP reset or EOF):
- The `_pipe_box_to_cloud` coroutine catches the write error
- `ModeManager.record_failure()` is called again (no effect in ONLINE)
- Both coroutines exit, Box connection cleanup runs

---

## HYBRID Mode

HYBRID starts online and automatically degrades to offline when the cloud becomes unreliable.

### State Machine

```
         ┌──────────────────────────────────────────────────┐
         │                                                  │
         v                                                  │
    ┌─────────┐  N failures                ┌──────────┐    │
    │ ONLINE  │ ────────────────────────>  │ OFFLINE  │    │
    │(runtime)│                            │ (runtime)│    │
    └─────────┘  cloud success             └──────────┘    │
         ^       (from retry attempt)           │          │
         └───────────────────────────────────── ┘          │
                                                            │
         retry_interval timer ──────────────────────────── ┘
         (triggers probe attempt)
```

### Transition: ONLINE to OFFLINE

The transition happens when `fail_count >= hybrid_fail_threshold`.

Each cloud connection failure (timeout or connection refused) calls `record_failure()`:

```python
def record_failure(self, reason=None):
    if not self.is_hybrid_mode():
        return
    self.fail_count += 1
    if self.fail_count >= self.fail_threshold:
        if not self.in_offline:
            # flip to offline
            self.in_offline = True
            self.last_offline_time = time.time()
            self.runtime_mode = ConnectionMode.OFFLINE
```

Once `in_offline = True`, new Box connections go to `_pipe_box_offline` which sends local ACK responses.

### Transition: OFFLINE to ONLINE (Retry)

`should_try_cloud()` is called on each new Box connection:

```python
def should_try_cloud(self):
    if self.configured_mode == "offline":
        return False
    if self.configured_mode == "online":
        return True
    # HYBRID:
    if not self.in_offline:
        return True
    elapsed = time.time() - self.last_offline_time
    if elapsed >= self.retry_interval:
        # retry interval passed, try cloud
        return True
    return False
```

When `should_try_cloud()` returns `True` after the retry interval, the proxy attempts to open a cloud connection. If it succeeds, `record_success()` flips back to ONLINE:

```python
def record_success(self):
    if not self.is_hybrid_mode():
        return
    if self.in_offline:
        self.in_offline = False
        self.runtime_mode = ConnectionMode.ONLINE
    self.fail_count = 0
```

If the probe fails, `record_failure()` resets `last_offline_time` (extends the offline window). The counter keeps incrementing but the threshold is already crossed, so it just keeps `in_offline = True`.

### Local ACK in HYBRID Offline State

When in the offline state, `_pipe_box_offline` handles the Box session:

1. Reads frames from Box into buffer
2. Calls `_handle_offline_frames` for each complete frame
3. Builds a local ACK via `build_local_ack(table_name)`
4. Writes the ACK back to the Box immediately

The Box gets valid ACK responses and keeps sending data. MQTT publishing still works: `_process_frame` is called for every frame regardless of mode.

### Configuration

```yaml
proxy_mode: hybrid
hybrid_retry_interval: 60      # seconds between cloud retry attempts
hybrid_fail_threshold: 1       # failures before switching offline
```

With `hybrid_fail_threshold: 1`, a single cloud failure switches to offline. Raise this to 3-5 if you have occasional brief cloud blips and don't want to flap.

---

## OFFLINE Mode

The proxy never attempts to connect to the cloud. All Box sessions get local ACK responses.

### Behavior

1. Box connects to proxy port
2. `should_try_cloud()` returns `False` immediately
3. `_pipe_box_offline` takes over the session
4. Every frame gets a local ACK
5. MQTT publishing works normally

This is useful when:
- The OIG cloud service is permanently unavailable
- You want to run in air-gapped mode
- You're testing MQTT publishing without a real cloud account

### Flow Diagram

```
BOX ──connect──> ProxyServer
                    │
                    ├── should_try_cloud() = False
                    │
                    └── _pipe_box_offline:
                          read Box data into buffer
                          extract complete frames
                          _handle_offline_frames:
                            build_local_ack(table_name) -> ACK bytes
                            write ACK to Box
                          _process_frame -> on_frame() -> MQTT
```

### Configuration

```yaml
proxy_mode: offline
```

`hybrid_*` and `cloud_ack_timeout` parameters are irrelevant in this mode.

---

## Local ACK Generation

When the proxy must respond to the Box without a real cloud ACK, it uses `build_local_ack()` from `proxy/local_ack.py`.

The function selects the appropriate frame type based on the table name:

| Table | Local ACK response |
|---|---|
| `END` | END frame with `Time`, `UTCTime`, and `GetActual` |
| `IsNewSet` | ACK frame (queue empty) or END frame with timestamp (when there's queued data) |
| `IsNewWeather` | END frame |
| `IsNewFW` | END frame |
| `tbl_actual` | ACK with `GetActual` command (requests fresh data) |
| `tbl_*` (other) | Plain ACK frame |
| (anything else) | Plain ACK frame (fallback) |

The response is a complete OIG protocol frame with length prefix and CRC. The Box treats it as a valid cloud response.

### IsNewSet Special Case

`IsNewSet` is the Box's "poll for new settings" message. In local ACK mode:

- If there are pending Twin settings in the queue: send `END` with a timestamp (signals the Box to expect a settings push)
- If the queue is empty: send a plain `ACK` (signals nothing to do)

This allows the Twin settings push to work in OFFLINE mode too.

---

## Mode Queries

The following `ModeManager` methods are used throughout the codebase:

| Method | Returns | Used by |
|---|---|---|
| `should_try_cloud()` | bool | `_handle_box_connection` to decide whether to open cloud TCP |
| `is_offline()` | bool | `_pipe_box_to_cloud` when cloud drops mid-session |
| `is_hybrid_mode()` | bool | `record_failure`, `record_success` to guard HYBRID-only logic |
| `force_offline_enabled()` | bool | `is_offline()` to shortcut OFFLINE mode |
| `record_failure(reason)` | None | On any cloud connection error |
| `record_success()` | None | On successful cloud connection |
| `get_current_mode()` | ConnectionMode | Async, used for status reporting |

---

## Mode in MQTT Status

The current mode is published periodically to `proxy_status:mode` in the proxy status MQTT payload. Possible values:

- `online` (runtime ONLINE)
- `offline` (runtime OFFLINE, either configured or HYBRID degraded)

The configured mode appears separately in `proxy_status:status`.

---

## Example: HYBRID Failover Timeline

```
T=0    Box connects, proxy opens cloud TCP → success → ONLINE
T=60   Box reconnects → cloud still ok → ONLINE, fail_count stays 0
T=120  Cloud goes down
T=121  Box connects, proxy tries cloud → connection refused → fail_count=1
       fail_count >= threshold(1) → switch to OFFLINE
       last_offline_time = T=121
T=122  Box reconnects → should_try_cloud() checks elapsed(1s) < interval(60s) → False
       → _pipe_box_offline → local ACK
T=181  Box reconnects → elapsed(60s) >= interval(60s) → should_try_cloud() = True
       Probe: cloud still down → fail_count=2, last_offline_time reset to T=181
T=241  Next retry window opens
T=300  Cloud recovers
T=301  Box reconnects → probe → cloud connection SUCCESS → record_success()
       in_offline = False, fail_count = 0, runtime_mode = ONLINE
T=302  Next Box connection → normal forwarding resumes
```

---

## Choosing a Mode

**Use `online` if:** Your cloud connection is stable and you want minimal complexity. You won't get local MQTT data if the cloud goes down, but the setup is simpler.

**Use `hybrid` if:** Your cloud is sometimes unavailable (scheduled maintenance, ISP issues) and you want Home Assistant to keep receiving sensor data during outages. This is the recommended mode for most installations.

**Use `offline` if:** You don't have or want a cloud connection, or you're running the proxy in a test environment. All MQTT data will be available but cloud sync won't happen.
