## Task T2 — Legacy control MQTT topic dependency matrix

Scope audited:
- Repository code/docs/tests in this worktree
- Home Assistant host config via SSH (`/homeassistant` and `/homeassistant/.storage`)

Reference points:
- `addon/oig-proxy/control_pipeline.py`
- `addon/oig-proxy/control_settings.py`
- `CHANGELOG.md` (`1.3.11` historical contract)

### Topic matrix

| Topic | Internal producer(s) | Internal consumer(s) | External producer(s) | External consumer(s) | Evidence | Classification |
|---|---|---|---|---|---|---|
| `oig_local/oig_proxy/control/set` | **None found (current code path)** | `TwinMQTTHandler` wildcard subscription on `oig_local/+/+/set` (matches this topic shape) in `addon/oig-proxy/digital_twin.py` | HA automations/scripts/Node-RED/CLI publishers (historical contract from changelog; concrete YAML reference not found on scanned HA host) | N/A | `digital_twin.py` (`set_topic = f"{MQTT_NAMESPACE}/+/+/set"`), `CHANGELOG.md` line with `control/set` contract, `config.py` default topic constants | **Active compatibility risk** (legacy topic can still be consumed indirectly via wildcard, but semantics changed) |
| `oig_local/oig_proxy/control/result` | **None found** (no active publisher in current runtime code) | **None found in repo** | N/A | HA automations/dashboards/external monitoring may subscribe (unknown) | `config.py` constant only; no runtime usage found by repo grep | **Dormant/contract drift risk** |
| `oig_local/oig_proxy/control/status/#` | **None found** (no active publisher to `control/status/...`) | **None found in repo** | N/A | HA automations/dashboards may subscribe (unknown) | `config.py` status prefix constant only; no runtime usage found by repo grep | **Dormant/contract drift risk** |

### Internal hit inventory (for classification completeness)

1. **Runtime code**
   - `addon/oig-proxy/config.py` contains legacy constants:
     - `CONTROL_MQTT_SET_TOPIC` default `oig_local/oig_proxy/control/set`
     - `CONTROL_MQTT_RESULT_TOPIC` default `oig_local/oig_proxy/control/result`
     - `CONTROL_MQTT_STATUS_PREFIX` default `oig_local/oig_proxy/control/status`
   - `addon/oig-proxy/digital_twin.py` subscribes to `oig_local/+/+/set` (wildcard), which can receive legacy `.../control/set` traffic.
   - `control_pipeline.py` / `control_settings.py` currently do **not** implement direct legacy topic publish/subscribe behavior.

2. **Docs/history**
   - `CHANGELOG.md` (`1.3.11`) documents legacy contract: `control/set` + `control/result` with queue/dedupe/noop/whitelist/timeouts.

3. **Tests (test-only references)**
   - Multiple tests set `ctrl.set_topic`, `ctrl.result_topic`, `ctrl.status_prefix` to `oig/control/...` fixtures.
   - `tests/test_proxy_internal.py` explicitly notes that `on_mqtt_message` was removed from slimmed `ControlPipeline` API.
   - These references are test scaffolding/compatibility fixtures, not proof of active legacy runtime producers.

### Blast radius assessment

#### Internal blast radius
- **Low to medium** for core runtime (no direct active publisher/consumer for legacy result/status topics found).
- **Medium** for ingress semantics: legacy `control/set` can still match twin wildcard subscription, potentially changing behavior versus historical control pipeline contract.

#### External blast radius
- **High uncertainty** for existing HA users relying on:
  - `control/result` listeners (automations, UI, scripts)
  - `control/status/#` listeners
  - legacy `control/set` payload shape assumptions

If legacy topic names/payload behavior are changed without compatibility layer, external automations may silently stop receiving expected result/status signals or trigger wrong routing semantics.

### Explicit risk items (unknown dependencies)

1. **Unknown external consumers of `.../control/result`**
   - Not discoverable from repository; no confirmed producer currently in code.
2. **Unknown external consumers of `.../control/status/#`**
   - Not discoverable from repository; no confirmed producer currently in code.
3. **Unknown external publishers still targeting `.../control/set` with legacy payload schema**
   - Topic may be consumed via wildcard twin handler, but contract compatibility is not guaranteed.
