# Twin Runtime Activation Design

## Config Chain: options → run → config.py → proxy.py

```
config.json (HA addon UI)
  └─ schema defines types + defaults
       └─ `run` (bash) reads via bashio::config, exports env
            └─ config.py reads os.getenv() with safe defaults
                 └─ validate_startup_guards() blocks invalid combos
                      └─ proxy.py OIGProxy.__init__() creates Twin objects
```

### Layer 1: config.json (HA Addon Options)

Twin env vars are exposed as optional addon settings (`bool?` / `str?`):

| Option Key              | Schema Type                              | Default  |
|-------------------------|------------------------------------------|----------|
| `twin_enabled`          | `bool?`                                  | `false`  |
| `twin_kill_switch`      | `bool?`                                  | `false`  |
| `twin_cloud_aligned`    | `bool?`                                  | `false`  |
| `local_control_routing` | `list(auto\|force_twin\|force_cloud)?`   | `auto`   |
| `twin_verbose_logging`  | `bool?`                                  | `false`  |

### Layer 2: run (bash entrypoint)

Each option is read via `bashio::config`, null/empty-checked, and exported:

```bash
TWIN_ENABLED_RAW=$(bashio::config 'twin_enabled' 2>/dev/null)
# → normalize to "true"/"false" → export TWIN_ENABLED
```

### Layer 3: config.py (Python constants)

Already implemented with safe helpers:

```python
TWIN_ENABLED = _get_bool_env("TWIN_ENABLED", False)
TWIN_KILL_SWITCH = _get_bool_env("TWIN_KILL_SWITCH", False)
TWIN_CLOUD_ALIGNED = _get_bool_env("TWIN_CLOUD_ALIGNED", False)
LOCAL_CONTROL_ROUTING = _get_str_env(
    "LOCAL_CONTROL_ROUTING", "auto",
    ["auto", "force_twin", "force_cloud"]
)
```

### Layer 4: validate_startup_guards()

Called from `main.py` before OIGProxy construction. On failure, raises
`ValueError` with concatenated error messages → `sys.exit(1)`.

### Layer 5: proxy.py OIGProxy.__init__()

```python
self._twin_kill_switch = bool(TWIN_KILL_SWITCH)
self._twin_enabled = bool(TWIN_ENABLED) and not self._twin_kill_switch
twin_config = DigitalTwinConfig(device_id=device_id)
self._twin = DigitalTwin(config=twin_config) if self._twin_enabled else None
```

---

## Environment Variable Matrix

### Primary Twin Controls

| Variable                | Type   | Default  | Valid Values                      | Effect                                                   |
|-------------------------|--------|----------|-----------------------------------|----------------------------------------------------------|
| `TWIN_ENABLED`          | bool   | `false`  | `true`/`false`                    | Master switch. When false, no Twin objects are created.   |
| `TWIN_KILL_SWITCH`      | bool   | `false`  | `true`/`false`                    | Emergency override. When true, disables Twin even if enabled. |
| `LOCAL_CONTROL_ROUTING` | str    | `auto`   | `auto`, `force_twin`, `force_cloud` | Routing policy for local control commands.               |
| `TWIN_CLOUD_ALIGNED`    | bool   | `false`  | `true`/`false`                    | Cloud-aligned ACK validation (simplified INV checks).    |

### Twin Timing Parameters

| Variable                          | Type   | Default  | Valid Values  | Effect                                              |
|-----------------------------------|--------|----------|---------------|-----------------------------------------------------|
| `TWIN_ACK_DEADLINE_SECONDS`       | float  | `30.0`   | `> 0`         | Timeout waiting for BOX ACK after delivery.         |
| `TWIN_APPLIED_DEADLINE_SECONDS`   | float  | `60.0`   | `> 0`, `≥ ack`| Timeout waiting for tbl_events confirmation.        |
| `TWIN_VERBOSE_LOGGING`            | bool   | `false`  | `true`/`false`| Extra debug logging for twin operations.            |

---

## Activation Truth Table (Complete)

| # | TWIN_ENABLED | TWIN_KILL_SWITCH | LOCAL_CONTROL_ROUTING | TWIN_CLOUD_ALIGNED | Result                     | Twin Created | Routing Target    |
|---|-------------|------------------|----------------------|--------------------|-----------------------------|-------------|-------------------|
| 1 | false       | false            | auto                 | false              | Legacy mode (no twin)       | No          | cloud/local       |
| 2 | true        | false            | auto                 | false              | Twin active, auto-route     | Yes         | auto (mode-based) |
| 3 | true        | false            | force_twin           | false              | Twin active, always twin    | Yes         | twin              |
| 4 | true        | false            | force_cloud          | false              | Twin active, always cloud   | Yes         | cloud             |
| 5 | true        | true             | auto                 | false              | Kill switch → no twin       | No          | cloud/local       |
| 6 | true        | false            | auto                 | true               | Cloud-aligned twin          | Yes         | auto + cloud ACK  |
| 7 | true        | true             | force_twin           | false              | **INVALID** → startup error | —           | —                 |
| 8 | false       | false            | force_twin           | false              | **INVALID** → startup error | —           | —                 |
| 9 | false       | false            | auto                 | true               | **INVALID** → startup error | —           | —                 |
| 10| false       | true             | auto                 | false              | Legacy (kill switch is noop) | No         | cloud/local       |
| 11| true        | false            | force_twin           | true               | Cloud-aligned + force_twin  | Yes         | twin              |
| 12| true        | true             | force_cloud          | false              | Kill switch, force_cloud ok | No          | cloud             |

### Routing Resolution Logic (auto mode)

When `LOCAL_CONTROL_ROUTING=auto`, routing depends on proxy mode:

| Proxy Mode | Twin Available | Cloud Available | Routing Target |
|-----------|---------------|-----------------|----------------|
| ONLINE    | Yes           | Yes             | cloud           |
| ONLINE    | Yes (session active) | Yes      | twin            |
| HYBRID    | Yes           | Yes             | cloud           |
| HYBRID    | Yes           | No              | twin            |
| HYBRID    | No            | No              | local           |
| OFFLINE   | Yes           | —               | twin            |
| OFFLINE   | No            | —               | local           |

---

## Invalid Guard Cases

### Guard 1: force_twin requires active twin

**Condition**: `LOCAL_CONTROL_ROUTING == "force_twin"` AND (`TWIN_ENABLED == false` OR `TWIN_KILL_SWITCH == true`)

**Error**: `"LOCAL_CONTROL_ROUTING=force_twin requires TWIN_ENABLED=true and TWIN_KILL_SWITCH=false"`

**Rationale**: force_twin routing is meaningless without a functioning twin. Startup must fail deterministically rather than silently falling back to local routing.

### Guard 2: cloud_aligned requires twin

**Condition**: `TWIN_CLOUD_ALIGNED == true` AND `TWIN_ENABLED == false`

**Error**: `"TWIN_CLOUD_ALIGNED=true requires TWIN_ENABLED=true"`

**Rationale**: Cloud-aligned ACK mode is a Twin feature. Enabling it without Twin creates a configuration that does nothing but could confuse operators.

### Guard 3: cloud_aligned incompatible with kill switch

**Condition**: `TWIN_CLOUD_ALIGNED == true` AND `TWIN_KILL_SWITCH == true`

**Error**: `"TWIN_CLOUD_ALIGNED=true is incompatible with TWIN_KILL_SWITCH=true"`

**Rationale**: Kill switch disables Twin at runtime. Cloud-aligned mode requires Twin to be active for its simplified ACK validation path.

### Guard 4: ACK deadline must be positive

**Condition**: `TWIN_ACK_DEADLINE_SECONDS <= 0`

**Error**: `"TWIN_ACK_DEADLINE_SECONDS must be > 0"`

### Guard 5: Applied deadline must be positive

**Condition**: `TWIN_APPLIED_DEADLINE_SECONDS <= 0`

**Error**: `"TWIN_APPLIED_DEADLINE_SECONDS must be > 0"`

### Guard 6: Applied deadline must be >= ACK deadline

**Condition**: `TWIN_APPLIED_DEADLINE_SECONDS < TWIN_ACK_DEADLINE_SECONDS`

**Error**: `"TWIN_APPLIED_DEADLINE_SECONDS must be >= TWIN_ACK_DEADLINE_SECONDS"`

**Rationale**: The applied timeout runs after ACK is received. If applied < ack, the timing window is logically impossible.

---

## Startup Failure Behavior

When `validate_startup_guards()` detects invalid combinations:

1. All detected errors are collected (not fail-fast)
2. Errors are joined with `"; "` separator
3. `ValueError` is raised with combined message
4. `main.py` catches at top level → logs `❌ Fatal: ...` → `sys.exit(1)`
5. HA addon shows error in log, addon stops

This is a **hard fail** — the proxy will NOT start with invalid config. There is no graceful degradation for contradictory settings.

---

## Legacy Path Preservation

The `TWIN_ENABLED=false` (default) path remains unchanged:
- No Twin objects created
- No Twin MQTT handler
- `_resolve_local_control_routing()` returns `"cloud"` or `"local"` per existing mode logic
- All existing tests continue to pass

The legacy path is NOT removed by this design. Twin activation is purely opt-in.
