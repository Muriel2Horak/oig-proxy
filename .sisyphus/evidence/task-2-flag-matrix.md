# Feature Flag Matrix - Task 2

## Overview
This document defines the feature flags for the thin-pass-through twin sidecar refactor.
Flags are designed for safe migration with explicit rollback paths.

---

## Flag Definitions

### 1. THIN_PASS_THROUGH

| Attribute | Value |
|-----------|-------|
| **Flag Name** | `THIN_PASS_THROUGH` |
| **Environment Variable** | `THIN_PASS_THROUGH` |
| **Default Value** | `false` |
| **Type** | `bool` |
| **Purpose** | Enable thin pass-through mode - minimal proxy that forwards XML frames with minimal processing |
| **Migration Safety** | Default `false` ensures existing behavior is preserved during rollout |

**Expected Values:**
- `false` (default): Full proxy mode with all features
- `true`: Thin pass-through mode

---

### 2. SIDECAR_ACTIVATION

| Attribute | Value |
|-----------|-------|
| **Flag Name** | `SIDECAR_ACTIVATION` |
| **Environment Variable** | `SIDECAR_ACTIVATION` |
| **Default Value** | `false` |
| **Type** | `bool` |
| **Purpose** | Enable twin sidecar mode - separate process handling local control routing |
| **Migration Safety** | Default `false` ensures backward compatibility |

**Expected Values:**
- `false` (default): Legacy single-process mode
- `true`: Twin sidecar mode active

---

### 3. LEGACY_FALLBACK

| Attribute | Value |
|-----------|-------|
| **Flag Name** | `LEGACY_FALLBACK` |
| **Environment Variable** | `LEGACY_FALLBACK` |
| **Default Value** | `true` |
| **Type** | `bool` |
| **Purpose** | Enable legacy fallback behavior for backward compatibility |
| **Migration Safety** | Default `true` ensures existing deployments continue working |

**Expected Values:**
- `true` (default): Legacy fallback enabled
- `false`: Modern behavior only

---

## Rollback Sequence

The rollback priority (from highest to lowest) is:

```
LEGACY_FALLBACK → SIDECAR_ACTIVATION → THIN_PASS_THROUGH
```

### Rollback Logic

1. **LEGACY_FALLBACK** (Priority 1 - Highest)
   - If `LEGACY_FALLBACK=true`: Use legacy behavior for all edge cases
   - Rollback target: Full backward compatibility mode

2. **SIDECAR_ACTIVATION** (Priority 2)
   - If `SIDECAR_ACTIVATION=false`: Disable sidecar, use inline mode
   - Rollback target: Single-process mode (existing architecture)

3. **THIN_PASS_THROUGH** (Priority 3 - Lowest)
   - If `THIN_PASS_THROUGH=false`: Use full proxy with all features
   - Rollback target: Full-featured proxy mode

---

## Implementation Pattern (Reference from config.py)

Based on existing patterns in `addon/oig-proxy/config.py`:

```python
# Helper function (already exists)
def _get_bool_env(name: str, default: bool) -> bool:
    """Vrátí bool z env proměnné s bezpečným fallbackem."""
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = str(raw).strip()
    if raw.lower() in ("true", "1", "yes", "on"):
        return True
    if raw.lower() in ("false", "0", "no", "off"):
        return False
    return default

# Feature flags (to be added to config.py)
THIN_PASS_THROUGH = _get_bool_env("THIN_PASS_THROUGH", False)
SIDECAR_ACTIVATION = _get_bool_env("SIDECAR_ACTIVATION", False)
LEGACY_FALLBACK = _get_bool_env("LEGACY_FALLBACK", True)
```

---

## Flag Dependencies

| Flag | Depends On | Conflict With |
|------|------------|---------------|
| `THIN_PASS_THROUGH` | None | None |
| `SIDECAR_ACTIVATION` | None | None |
| `LEGACY_FALLBACK` | None | None |

---

## Validation Rules

1. All flags are boolean - use `_get_bool_env()` helper
2. Default values chosen for migration safety:
   - `THIN_PASS_THROUGH=false`: Preserve full proxy behavior
   - `SIDECAR_ACTIVATION=false`: Preserve single-process mode
   - `LEGACY_FALLBACK=true`: Preserve backward compatibility

---

## References

- Existing config patterns: `addon/oig-proxy/config.py`
- Mode policy: `addon/oig-proxy/hybrid_mode.py`
- Plan: `.sisyphus/plans/proxy-thin-pass-through-twin-sidecar-refactor.md`