# Local GetActual Option Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in Home Assistant add-on option that lets the proxy inject `GetActual` frames to the connected OIG box at a configurable 10-second cadence.

**Architecture:** The HA add-on config exposes two new options, `run` exports them to environment variables, and `Config` parses them into typed runtime fields. `ProxyServer` starts one cancellable local GetActual task per active box TCP session when the feature is enabled; injected frames reuse `build_getactual_frame()`, are captured as `proxy_to_box`, and increment the existing telemetry direction counter.

**Tech Stack:** Python 3.11, asyncio TCP proxy, Home Assistant add-on `config.json`/`bashio`, pytest, pylint, mypy, Bandit, existing `deploy_to_haos.sh`.

## Global Constraints

- Base branch is `main`.
- Current released add-on version is `2.1.0`; target test version is `2.1.1`.
- Default `local_getactual_enabled` is `false`.
- Default `local_getactual_interval_s` is `10`.
- Minimum effective local GetActual interval is `10` seconds.
- Enable/disable is applied through HA add-on configuration and add-on restart.
- No runtime behavior changes when `local_getactual_enabled` is `false`.
- Always use PR workflow for remote changes.
- Before PR, run basic tests, MNP/smoke validation, lint, pylint, mypy, security checks, and coverage with threshold `80`.
- Deploy first to our HA through `./deploy_to_haos.sh` and test enable/disable on our box.

---

## File Structure

- Modify `addon/oig-proxy/config.json`: add HA options/schema and bump version to `2.1.1`.
- Modify `addon/oig-proxy/run`: read new HA options through `bashio::config` and export `LOCAL_GETACTUAL_ENABLED` plus `LOCAL_GETACTUAL_INTERVAL_S`.
- Modify `addon/oig-proxy/config.py`: add typed runtime defaults and env parsing with a 10-second lower bound.
- Modify `addon/oig-proxy/proxy/server.py`: add local GetActual task helpers and start/cancel them per box session.
- Modify `docs/v2/configuration.md`: document the new config options and restart behavior.
- Modify `tests/v2/test_addon_dns_config.py`: cover config defaults, env overrides, clamp behavior, add-on config schema, and run script exports.
- Modify `tests/v2/test_proxy/test_server.py`: cover disabled loop, enabled immediate send, capture/telemetry visibility, write failure, and cancellation helper.

---

### Task 1: Configuration Surface

**Files:**
- Modify: `addon/oig-proxy/config.json`
- Modify: `addon/oig-proxy/run`
- Modify: `addon/oig-proxy/config.py`
- Modify: `docs/v2/configuration.md`
- Test: `tests/v2/test_addon_dns_config.py`

**Interfaces:**
- Produces `Config.local_getactual_enabled: bool`.
- Produces `Config.local_getactual_interval_s: int`, clamped to at least `10`.
- Produces env vars `LOCAL_GETACTUAL_ENABLED` and `LOCAL_GETACTUAL_INTERVAL_S` for `Config`.
- Consumed by Task 2 from `ProxyServer.config`.

- [ ] **Step 1: Write failing config tests**

Add `import json` at the top of `tests/v2/test_addon_dns_config.py`.

Append these tests to `tests/v2/test_addon_dns_config.py`:

```python
def test_config_defaults_local_getactual_disabled(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_GETACTUAL_ENABLED", raising=False)
    monkeypatch.delenv("LOCAL_GETACTUAL_INTERVAL_S", raising=False)

    config = Config()

    assert config.local_getactual_enabled is False
    assert config.local_getactual_interval_s == 10


def test_config_reads_local_getactual_env(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_GETACTUAL_ENABLED", "true")
    monkeypatch.setenv("LOCAL_GETACTUAL_INTERVAL_S", "15")

    config = Config()

    assert config.local_getactual_enabled is True
    assert config.local_getactual_interval_s == 15


def test_config_clamps_local_getactual_interval(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_GETACTUAL_ENABLED", "1")
    monkeypatch.setenv("LOCAL_GETACTUAL_INTERVAL_S", "3")

    config = Config()

    assert config.local_getactual_enabled is True
    assert config.local_getactual_interval_s == 10


def test_addon_config_exposes_local_getactual_options() -> None:
    addon_config = json.loads((ADDON_DIR / "config.json").read_text(encoding="utf-8"))

    assert addon_config["version"] == "2.1.1"
    assert addon_config["options"]["local_getactual_enabled"] is False
    assert addon_config["options"]["local_getactual_interval_s"] == 10
    assert addon_config["schema"]["local_getactual_enabled"] == "bool?"
    assert addon_config["schema"]["local_getactual_interval_s"] == "int?"


def test_run_exports_local_getactual_options() -> None:
    run_script = (ADDON_DIR / "run").read_text(encoding="utf-8")

    assert "LOCAL_GETACTUAL_ENABLED_RAW=$(bashio::config 'local_getactual_enabled')" in run_script
    assert "export LOCAL_GETACTUAL_ENABLED=\"true\"" in run_script
    assert "export LOCAL_GETACTUAL_ENABLED=\"false\"" in run_script
    assert "LOCAL_GETACTUAL_INTERVAL_RAW=$(bashio::config 'local_getactual_interval_s')" in run_script
    assert "LOCAL_GETACTUAL_INTERVAL_RAW=10" in run_script
    assert "export LOCAL_GETACTUAL_INTERVAL_S=$LOCAL_GETACTUAL_INTERVAL_RAW" in run_script
```

- [ ] **Step 2: Run config tests to verify red**

Run:

```bash
python -m pytest tests/v2/test_addon_dns_config.py -q
```

Expected: FAIL because `Config.local_getactual_enabled` and `Config.local_getactual_interval_s` do not exist, `config.json` is still `2.1.0`, and `run` does not export local GetActual env vars.

- [ ] **Step 3: Update add-on config schema**

In `addon/oig-proxy/config.json`, change:

```json
"version": "2.1.0",
```

to:

```json
"version": "2.1.1",
```

Add these entries to `"options"` after `"cloud_ack_timeout": 1800.0,`:

```json
"local_getactual_enabled": false,
"local_getactual_interval_s": 10,
```

Add these entries to `"schema"` after `"cloud_ack_timeout": "float",`:

```json
"local_getactual_enabled": "bool?",
"local_getactual_interval_s": "int?",
```

- [ ] **Step 4: Export env vars in run**

In `addon/oig-proxy/run`, after the `CLOUD_ACK_TIMEOUT` export block, add:

```bash
# LOCAL_GETACTUAL: opt-in proxy-injected GetActual cadence
LOCAL_GETACTUAL_ENABLED_RAW=$(bashio::config 'local_getactual_enabled')
if [ "$LOCAL_GETACTUAL_ENABLED_RAW" = "true" ] || [ "$LOCAL_GETACTUAL_ENABLED_RAW" = "1" ]; then
    export LOCAL_GETACTUAL_ENABLED="true"
else
    export LOCAL_GETACTUAL_ENABLED="false"
fi

LOCAL_GETACTUAL_INTERVAL_RAW=$(bashio::config 'local_getactual_interval_s')
if [ -z "$LOCAL_GETACTUAL_INTERVAL_RAW" ] || [ "$LOCAL_GETACTUAL_INTERVAL_RAW" = "null" ]; then
    LOCAL_GETACTUAL_INTERVAL_RAW=10
fi
export LOCAL_GETACTUAL_INTERVAL_S=$LOCAL_GETACTUAL_INTERVAL_RAW
```

Change the nearby comment:

```bash
# Env pro proxy - all 21 parameters
```

to:

```bash
# Env pro proxy - HA add-on parameters
```

- [ ] **Step 5: Parse env vars in Config**

In `addon/oig-proxy/config.py`, add class defaults after `cloud_ack_timeout: float = 30.0`:

```python
    local_getactual_enabled: bool = False
    local_getactual_interval_s: int = 10
```

In `Config.__init__`, after `self.cloud_ack_timeout = float(...)`, add:

```python
        self.local_getactual_enabled = (
            os.environ.get("LOCAL_GETACTUAL_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        try:
            local_getactual_interval_s = int(
                float(os.environ.get("LOCAL_GETACTUAL_INTERVAL_S", "10"))
            )
        except ValueError:
            local_getactual_interval_s = 10
        self.local_getactual_interval_s = max(10, local_getactual_interval_s)
```

- [ ] **Step 6: Document the new options**

In `docs/v2/configuration.md`, add these rows to the parameter table after `cloud_ack_timeout`:

```markdown
| `local_getactual_enabled` | `LOCAL_GETACTUAL_ENABLED` | bool? | `false` | Opt-in proxy-injected `GetActual` frames for boxes that do not receive cloud `GetActual` often enough |
| `local_getactual_interval_s` | `LOCAL_GETACTUAL_INTERVAL_S` | int? | `10` | Seconds between proxy-injected `GetActual` frames when local GetActual is enabled; values below 10 are clamped to 10 |
```

Change:

```markdown
**21 parameters total.**
```

to:

```markdown
**23 parameters total.**
```

Add this section after `cloud_ack_timeout`:

```markdown
### `local_getactual_enabled` / `local_getactual_interval_s`

These options are an opt-in workaround for boxes/FW combinations where the cloud does not send `GetActual` often enough while the box is otherwise online. When enabled, the proxy sends an ACK frame with `<ToDo>GetActual</ToDo>` to the connected box immediately after the TCP session is active and then every `local_getactual_interval_s` seconds.

The default is disabled, so upgrades do not change existing behavior. The default interval is 10 seconds and values below 10 seconds are clamped to 10 seconds. Changing either option requires saving the HA add-on configuration and restarting the add-on.
```

Add these env vars to the environment reference block:

```bash
export LOCAL_GETACTUAL_ENABLED=false
export LOCAL_GETACTUAL_INTERVAL_S=10
```

- [ ] **Step 7: Run config tests to verify green**

Run:

```bash
python -m pytest tests/v2/test_addon_dns_config.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

Run:

```bash
git add addon/oig-proxy/config.json addon/oig-proxy/run addon/oig-proxy/config.py docs/v2/configuration.md tests/v2/test_addon_dns_config.py
git commit -m "feat: expose local GetActual add-on options" -m "Změny: přidány HA options/env/config parser pro opt-in lokální GetActual s defaultem 10s a minimem 10s; dokumentace aktualizovaná na verzi 2.1.1. Testy: python -m pytest tests/v2/test_addon_dns_config.py -q."
```

Expected: commit succeeds on a feature branch, not directly on remote.

---

### Task 2: Proxy Local GetActual Scheduler

**Files:**
- Modify: `addon/oig-proxy/proxy/server.py`
- Test: `tests/v2/test_proxy/test_server.py`

**Interfaces:**
- Consumes `Config.local_getactual_enabled: bool`.
- Consumes `Config.local_getactual_interval_s: int`.
- Consumes `build_getactual_frame() -> bytes` from `addon/oig-proxy/protocol/frames.py`.
- Produces `ProxyServer._local_getactual_loop(box_writer, *, conn_id: int, peer: str | None) -> None`.
- Produces `ProxyServer._start_local_getactual_task(box_writer, *, conn_id: int, peer: str | None) -> asyncio.Task[None] | None`.
- Produces `ProxyServer._stop_local_getactual_task(task: asyncio.Task[None] | None) -> None`.

- [ ] **Step 1: Write failing scheduler tests**

Append these tests to `tests/v2/test_proxy/test_server.py`:

```python
@pytest.mark.asyncio
async def test_local_getactual_loop_disabled_does_not_write() -> None:
    cfg = make_config(local_getactual_enabled=False, local_getactual_interval_s=10)
    server = ProxyServer(cfg)
    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.write = MagicMock()
    box_writer.drain = AsyncMock()

    await server._local_getactual_loop(box_writer, conn_id=123, peer="box:1")

    box_writer.write.assert_not_called()
    box_writer.drain.assert_not_called()


@pytest.mark.asyncio
async def test_local_getactual_loop_sends_immediate_frame_and_records_direction() -> None:
    cfg = make_config(local_getactual_enabled=True, local_getactual_interval_s=10)
    telemetry = MagicMock()
    frame_capture = MagicMock()
    server = ProxyServer(cfg, frame_capture=frame_capture, telemetry_collector=telemetry)
    sent: list[bytes] = []
    sleep_calls: list[float] = []

    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.is_closing.return_value = False
    box_writer.write = MagicMock(side_effect=lambda data: sent.append(data))
    box_writer.drain = AsyncMock()

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        box_writer.is_closing.return_value = True

    with patch("proxy.server.asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)):
        await server._local_getactual_loop(box_writer, conn_id=456, peer="box:2")

    assert len(sent) == 1
    assert b"<Result>ACK</Result>" in sent[0]
    assert b"<ToDo>GetActual</ToDo>" in sent[0]
    assert sleep_calls == [10]
    frame_capture.capture.assert_called_once()
    assert frame_capture.capture.call_args.kwargs["direction"] == "proxy_to_box"
    telemetry.record_frame_direction.assert_called_once_with("proxy_to_box")


@pytest.mark.asyncio
async def test_local_getactual_loop_uses_minimum_interval() -> None:
    cfg = make_config(local_getactual_enabled=True, local_getactual_interval_s=3)
    server = ProxyServer(cfg)
    sleep_calls: list[float] = []

    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.is_closing.return_value = False
    box_writer.write = MagicMock()
    box_writer.drain = AsyncMock()

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        box_writer.is_closing.return_value = True

    with patch("proxy.server.asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)):
        await server._local_getactual_loop(box_writer, conn_id=789, peer="box:3")

    assert sleep_calls == [10]


@pytest.mark.asyncio
async def test_local_getactual_loop_stops_on_writer_error() -> None:
    cfg = make_config(local_getactual_enabled=True, local_getactual_interval_s=10)
    telemetry = MagicMock()
    server = ProxyServer(cfg, telemetry_collector=telemetry)
    box_writer = MagicMock(spec=asyncio.StreamWriter)
    box_writer.is_closing.return_value = False
    box_writer.write = MagicMock(side_effect=ConnectionResetError("gone"))
    box_writer.drain = AsyncMock()

    await server._local_getactual_loop(box_writer, conn_id=321, peer="box:4")

    box_writer.write.assert_called_once()
    box_writer.drain.assert_not_called()
    telemetry.record_frame_direction.assert_not_called()


@pytest.mark.asyncio
async def test_stop_local_getactual_task_cancels_running_task() -> None:
    cfg = make_config(local_getactual_enabled=True, local_getactual_interval_s=10)
    server = ProxyServer(cfg)

    async def wait_forever() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(wait_forever())

    await server._stop_local_getactual_task(task)

    assert task.cancelled()
```

- [ ] **Step 2: Run scheduler tests to verify red**

Run:

```bash
python -m pytest tests/v2/test_proxy/test_server.py -q
```

Expected: FAIL with `AttributeError: 'ProxyServer' object has no attribute '_local_getactual_loop'`.

- [ ] **Step 3: Import GetActual frame builder**

In `addon/oig-proxy/proxy/server.py`, change both relative and fallback imports from:

```python
from ..protocol.frames import build_setting_frame
```

and:

```python
from protocol.frames import build_setting_frame  # type: ignore[no-redef]
```

to:

```python
from ..protocol.frames import build_getactual_frame, build_setting_frame
```

and:

```python
from protocol.frames import build_getactual_frame, build_setting_frame  # type: ignore[no-redef]
```

- [ ] **Step 4: Add local GetActual task helpers**

In `addon/oig-proxy/proxy/server.py`, add these methods before `_handle_box_connection`:

```python
    def _local_getactual_interval_s(self) -> int:
        try:
            interval_s = int(getattr(self.config, "local_getactual_interval_s", 10))
        except (TypeError, ValueError):
            interval_s = 10
        return max(10, interval_s)

    def _start_local_getactual_task(
        self,
        box_writer: asyncio.StreamWriter,
        *,
        conn_id: int,
        peer: str | None,
    ) -> asyncio.Task[None] | None:
        if not bool(getattr(self.config, "local_getactual_enabled", False)):
            return None
        return asyncio.create_task(
            self._local_getactual_loop(box_writer, conn_id=conn_id, peer=peer),
            name=f"local-getactual-{conn_id}",
        )

    async def _stop_local_getactual_task(
        self,
        task: asyncio.Task[None] | None,
    ) -> None:
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _local_getactual_loop(
        self,
        box_writer: asyncio.StreamWriter,
        *,
        conn_id: int,
        peer: str | None,
    ) -> None:
        if not bool(getattr(self.config, "local_getactual_enabled", False)):
            return

        interval_s = self._local_getactual_interval_s()
        while not box_writer.is_closing():
            frame = build_getactual_frame()
            try:
                box_writer.write(frame)
                await box_writer.drain()
            except (OSError, ConnectionResetError) as exc:
                logger.debug("Local GetActual stopped for %s: %s", peer or "unknown", exc)
                break

            self._capture_frame(frame, "proxy_to_box", conn_id=conn_id, peer=peer)
            if self.telemetry_collector is not None:
                self.telemetry_collector.record_frame_direction("proxy_to_box")
            logger.debug(
                "📤 Sent local GetActual to BOX peer=%s conn_id=%s interval=%ss",
                peer or "unknown",
                conn_id,
                interval_s,
            )
            await asyncio.sleep(interval_s)
```

- [ ] **Step 5: Start/cancel task in offline session paths**

In each `_handle_box_connection` branch that calls `_pipe_box_offline`, add local task lifecycle around the call.

For the configured offline branch, change:

```python
            try:
                await self._pipe_box_offline(box_reader, box_writer, peer, session_id=session_id)
            finally:
```

to:

```python
            local_getactual_task = self._start_local_getactual_task(
                box_writer,
                conn_id=session_conn_id,
                peer=peer_str,
            )
            try:
                await self._pipe_box_offline(box_reader, box_writer, peer, session_id=session_id)
            finally:
                await self._stop_local_getactual_task(local_getactual_task)
```

Apply the same replacement pattern to the `offline_fallback_timeout` and `offline_fallback_oserror` branches.

- [ ] **Step 6: Start/cancel task in online cloud-connected session path**

In `_handle_box_connection`, after the `cloud_reader is None or cloud_writer is None` guard and before `pipe_tasks = [...]`, add:

```python
        local_getactual_task = self._start_local_getactual_task(
            box_writer,
            conn_id=session_conn_id,
            peer=peer_str,
        )
```

In the final cleanup block for the cloud-connected path, before `if self.twin_delivery is not None:`, add:

```python
            await self._stop_local_getactual_task(local_getactual_task)
```

- [ ] **Step 7: Run scheduler tests to verify green**

Run:

```bash
python -m pytest tests/v2/test_proxy/test_server.py -q
```

Expected: PASS.

- [ ] **Step 8: Run focused feature tests**

Run:

```bash
python -m pytest tests/v2/test_addon_dns_config.py tests/v2/test_proxy/test_server.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit Task 2**

Run:

```bash
git add addon/oig-proxy/proxy/server.py tests/v2/test_proxy/test_server.py
git commit -m "feat: inject opt-in local GetActual frames" -m "Změny: ProxyServer spouští cancellable lokální GetActual loop per box session, posílá frame přes existující build_getactual_frame a značí direction proxy_to_box pro capture/telemetrii. Testy: python -m pytest tests/v2/test_proxy/test_server.py -q; python -m pytest tests/v2/test_addon_dns_config.py tests/v2/test_proxy/test_server.py -q."
```

Expected: commit succeeds on the feature branch.

---

### Task 3: Verification, HA Deployment, and Observation

**Files:**
- No production file changes required.
- Uses: `deploy_to_haos.sh`
- Uses: `ci/ci.sh`
- Uses: reports under `reports/`

**Interfaces:**
- Consumes completed Tasks 1 and 2.
- Produces local verification evidence.
- Produces HA smoke-test evidence for enable/disable behavior on our box.

- [ ] **Step 1: Create or confirm feature branch**

Run:

```bash
git status --short --branch
git branch --show-current
```

Expected: current branch is a feature branch such as `codex/local-getactual-option`, not `main`. If still on `main`, run:

```bash
git switch -c codex/local-getactual-option
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
python -m pytest tests/v2/test_addon_dns_config.py tests/v2/test_proxy/test_server.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full local CI with 80% coverage gate**

Run:

```bash
COVERAGE_FAIL_UNDER=80 ./ci/ci.sh
```

Expected: PASS. If the inherited baseline fails below 80%, do not lower the threshold; record the exact coverage result and either add targeted tests until the command passes or treat it as a blocking quality issue before PR.

- [ ] **Step 4: Run explicit mypy**

Run:

```bash
PYTHONPATH=addon/oig-proxy python -m mypy addon/oig-proxy tests/v2
```

Expected: PASS.

- [ ] **Step 5: Run explicit pylint on changed areas**

Run:

```bash
PYTHONPATH=addon/oig-proxy python -m pylint addon/oig-proxy/config.py addon/oig-proxy/proxy/server.py tests/v2/test_addon_dns_config.py tests/v2/test_proxy/test_server.py --disable=import-outside-toplevel,unused-import,reimported,redefined-outer-name,line-too-long,f-string-without-interpolation,comparison-of-constants,comparison-with-itself,unused-argument,wrong-import-order
```

Expected: PASS or only existing repo-accepted warnings that are also accepted by `ci/ci.sh`. New warnings in changed code must be fixed.

- [ ] **Step 6: Run explicit security scan on changed production code**

Run:

```bash
python -m bandit -r addon/oig-proxy/config.py addon/oig-proxy/proxy/server.py
```

Expected: PASS with no new high or medium findings.

- [ ] **Step 7: Deploy to our HA**

Run:

```bash
./deploy_to_haos.sh
```

Expected: script uploads files, rebuilds add-on, starts `addon_d7b5d5b1_oig_proxy`, and final status is `running`.

- [ ] **Step 8: Verify HA add-on logs after deploy**

Run:

```bash
ssh ha "sudo docker logs addon_d7b5d5b1_oig_proxy --tail 80"
```

Expected: logs show the add-on starts without tracebacks and reports OIG Proxy v2 listening on port `5710`.

- [ ] **Step 9: Baseline disabled behavior on HA**

In the HA add-on configuration, use:

```yaml
local_getactual_enabled: false
local_getactual_interval_s: 10
capture_payloads: true
log_level: DEBUG
```

Restart the add-on:

```bash
ssh ha "ha apps restart d7b5d5b1_oig_proxy"
```

Observe for at least 10 minutes. Query capture:

```bash
ssh ha "sudo docker exec addon_d7b5d5b1_oig_proxy sqlite3 /data/payloads.db \"select ts,direction,table_name,raw from frames where direction='proxy_to_box' and raw like '%GetActual%' order by id desc limit 5;\""
```

Expected: no new proxy-injected `GetActual` rows are created while disabled. Existing rows from earlier tests can be distinguished by timestamp.

- [ ] **Step 10: Enable local GetActual on HA**

In the HA add-on configuration, use:

```yaml
local_getactual_enabled: true
local_getactual_interval_s: 10
capture_payloads: true
log_level: DEBUG
```

Restart the add-on:

```bash
ssh ha "ha apps restart d7b5d5b1_oig_proxy"
```

Observe logs:

```bash
ssh ha "sudo docker logs addon_d7b5d5b1_oig_proxy -f --tail 80"
```

Expected: on the next box session, logs include local GetActual debug messages and the proxy keeps running.

- [ ] **Step 11: Confirm injected frames and fresh data cadence**

Query capture:

```bash
ssh ha "sudo docker exec addon_d7b5d5b1_oig_proxy sqlite3 /data/payloads.db \"select ts,direction,table_name,raw from frames where direction='proxy_to_box' and raw like '%<ToDo>GetActual</ToDo>%' order by id desc limit 10;\""
```

Expected: recent rows show `direction='proxy_to_box'` and raw frames contain `<Result>ACK</Result><ToDo>GetActual</ToDo>`.

Check recent `tbl_actual` cadence:

```bash
ssh ha "sudo docker exec addon_d7b5d5b1_oig_proxy sqlite3 /data/payloads.db \"select ts,direction,table_name from frames where table_name='tbl_actual' order by id desc limit 20;\""
```

Expected: after enabling, recent `tbl_actual` rows are materially closer to the 10-second cadence than the previous 5-minute baseline.

- [ ] **Step 12: Disable again and verify stop**

In the HA add-on configuration, set:

```yaml
local_getactual_enabled: false
local_getactual_interval_s: 10
capture_payloads: true
log_level: INFO
```

Restart:

```bash
ssh ha "ha apps restart d7b5d5b1_oig_proxy"
```

Query for new rows after the restart timestamp:

```bash
ssh ha "sudo docker exec addon_d7b5d5b1_oig_proxy sqlite3 /data/payloads.db \"select ts,direction,table_name,raw from frames where direction='proxy_to_box' and raw like '%<ToDo>GetActual</ToDo>%' order by id desc limit 10;\""
```

Expected: no rows newer than the disable restart time.

- [ ] **Step 13: Commit verification evidence**

If verification generates a concise evidence note, create `reports/local-getactual-ha-verification.md` with:

```markdown
# Local GetActual HA Verification

Date: 2026-06-27
Branch: codex/local-getactual-option

## Local Checks

- Focused pytest: PASS
- Local CI with COVERAGE_FAIL_UNDER=80: PASS
- mypy: PASS
- pylint changed areas: PASS
- bandit changed production code: PASS

## HA Deploy

- deploy_to_haos.sh: PASS
- Add-on container: running
- Disabled mode: no new proxy_to_box GetActual frames
- Enabled mode: proxy_to_box GetActual frames observed
- Enabled tbl_actual cadence: improved versus 5-minute baseline
- Disabled again: no new proxy_to_box GetActual frames after restart
```

Run:

```bash
git add reports/local-getactual-ha-verification.md
git commit -m "test: record local GetActual HA verification" -m "Změny: přidána evidence lokálního CI a HA deploy ověření pro zapnutí/vypnutí local GetActual. Testy: focused pytest, local CI s COVERAGE_FAIL_UNDER=80, mypy, pylint, bandit, deploy_to_haos.sh a HA smoke ověření."
```

Expected: commit succeeds only after the evidence is filled with real PASS results.

---

## Self-Review

- Spec coverage: Task 1 covers HA options, env bridge, defaults, docs, version bump, and interval clamp. Task 2 covers immediate/periodic frame injection, disabled no-op behavior, capture direction, telemetry direction, writer error handling, and cancellation. Task 3 covers local verification, HA deploy, enable/disable observation, and PR-quality gates.
- Placeholder scan: The plan contains no undefined implementation steps; every code change includes concrete snippets and every verification command has an expected result.
- Type consistency: The field names are consistently `local_getactual_enabled`, `local_getactual_interval_s`, `LOCAL_GETACTUAL_ENABLED`, and `LOCAL_GETACTUAL_INTERVAL_S`. The scheduler methods consistently use `conn_id: int` and `peer: str | None`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-27-local-getactual-option.md`. Two execution options:

1. Subagent-Driven (recommended) - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. Inline Execution - execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
