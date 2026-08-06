# Local Setting Transaction Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unsafe in-memory local-setting path with a durable, device-bound, cloud-first transaction system whose delivery, retry, confirmation, audit, and test evidence match the approved passive-capture design.

**Architecture:** A standard-library SQLite store is the only mutable transaction source of truth; a per-device coordinator renders durable attempts and correlates session-bound ACK/NACK and exact execution events. A connection-local `SettingDialog` preserves cloud priority and raw byte order, while exact-device MQTT ingress, a startup hard gate, a serialized BOX writer, and hermetic loopback E2E tests enforce the external boundaries.

**Tech Stack:** Python 3.11, `asyncio`, standard-library `sqlite3` and `fcntl`, Paho MQTT, pytest/pytest-asyncio/pytest-cov, mypy, flake8, pylint, Bandit, Semgrep, Gitleaks, Safety, GitHub Actions, Home Assistant add-on shell/config files.

## Global Constraints

- Implement from base branch `main` on `codex/local-setting-transaction-hardening`; send remote changes only through a GitHub pull request.
- Current released add-on version is `2.1.1`; target release version is `2.2.0`.
- Treat `docs/superpowers/specs/2026-08-06-local-setting-transaction-hardening-design.md` as the authoritative behavior contract.
- Do not read protocol specifications or reuse earlier implementation assumptions to alter the passively observed wire contract.
- Do not send a command to a real BOX, cloud endpoint, Home Assistant MQTT broker, or telemetry broker; do not deploy to Home Assistant in this implementation.
- Add no runtime dependency; persistence uses Python's standard SQLite support.
- Keep `control_mqtt_enabled=false` as the shipped default and sample it once at process startup.
- Defaults are `control_ack_timeout_s=30`, `control_event_timeout_s=300`, `control_command_ttl_s=900`, and `control_max_attempts=8`; the hard attempt range is `1..8`.
- Internal defaults are `TWIN_DB_PATH=/data/twin_queue.db` and `CLOUD_DIALOG_TIMEOUT_S=30`.
- Use `/data/twin_queue.db.lock`, WAL, `synchronous=FULL`, foreign keys, and a `5000` ms SQLite busy timeout; any lock, migration, corruption, disk, or commit failure disables local control without replacing the database.
- Keep stable retry fields `ID`, `ID_Set`, `DT`, `ID_Device`, `NewValue`, `Confirm=New`, `TblName`, `TblItem`, and `ID_Server=9`; refresh only `TSec`, `ver`, and CRC.
- Treat `ACK/Setting` only as delivery evidence; publish setting state only after an exact committed `tbl_events`, `Type=Setting` confirmation.
- ONLINE and HYBRID always forward a valid `IsNewSet` poll to cloud first and may substitute only its correlated terminal cloud `END`; cloud Settings stay byte-identical.
- OFFLINE makes exactly one application response decision for each complete CRC-valid poll or ACK while the socket remains writable.
- Keep every stream assembly buffer and every held-frame queue at or below `1_048_576` bytes.
- Preserve unrelated raw bytes, CRLF, frame order, and coalesced frames; serialize every BOX write.
- Reject retained, wrong-device, unknown-device, malformed, oversized, non-finite, out-of-range, off-step, XML-invalid, wrong-direction, wrong-session, stale, and invalid-CRC control evidence before state mutation.
- Remove `/data/replay_setting_frame.xml` and every production raw-frame injection branch.
- Use TDD for each behavior slice and commit after each green slice; never stage the pre-existing untracked `.omx/` or `output/` paths.
- Require full `tests/v2`, MNP/smoke coverage, mypy, flake8, pylint, Bandit, Semgrep, Gitleaks, Safety, `git diff --check`, and statement plus branch coverage each strictly greater than `80.0%`.
- A Gitleaks secret, Bandit medium/high issue, Semgrep warning/error, dependency vulnerability, failed egress probe, unresolved OWASP row, or missing SI-1..SI-15 evidence blocks the PR unless a repository owner records a scoped risk acceptance.
- Use `git@github.com:Muriel2Horak/oig-proxy.git` with `gh-muriel`; do not use plain `gh` for PR operations.

---

## File Structure

### New production modules

- Create `addon/oig-proxy/twin/store.py`: schema v1, process lock, transactions, recovery, deadlines, attempts, ingress audit, event receipts, and immutable row mapping.
- Create `addon/oig-proxy/proxy/dialog.py`: connection-local request FIFO, cloud `IsNewSet` cycle, deferred END, held-frame queues, batch ownership, and protocol decisions.
- Create `addon/oig-proxy/proxy/writer.py`: one serialized BOX writer with semantic ownership, attempt write outcomes, capture linkage, and GetActual pausing.

### Modified production modules

- Modify `addon/oig-proxy/twin/state.py`: immutable enums, records, evidence contexts, claim/response/event decisions; remove `TwinQueue` at final cutover.
- Modify `addon/oig-proxy/twin/delivery.py`: per-device `TwinCoordinator`; remove process-global inflight and duplicate Setting serializer at final cutover.
- Modify `addon/oig-proxy/twin/handler.py`: exact-device subscriptions, retain-aware bounded ingress, Decimal/XML validation, and durable enqueue.
- Modify `addon/oig-proxy/twin/ack_parser.py`: typed strict ACK/NACK and event evidence over validated BOX frames.
- Modify `addon/oig-proxy/twin/__init__.py`: export durable interfaces and remove legacy queue exports at final cutover.
- Modify `addon/oig-proxy/protocol/crc.py`: exact inbound CRC validation.
- Modify `addon/oig-proxy/protocol/frame.py`: timestamped bounded stream assembly and validated-frame types.
- Modify `addon/oig-proxy/protocol/frames.py`: the sole deterministic, escaped Setting serializer.
- Modify `addon/oig-proxy/settings_constraints.py`: Decimal, finite, integer, range, step, alias, and canonical-string rules.
- Modify `addon/oig-proxy/proxy/server.py`: one semantic router per connection, cloud-first substitution, OFFLINE one-response routing, exact session/direction propagation, and replay removal.
- Modify `addon/oig-proxy/proxy/local_ack.py`: expose only canonical synthesized END/error responses used by OFFLINE routing.
- Modify `addon/oig-proxy/mqtt/client.py`: retain metadata, exact subscription restoration, disabled-control discovery cleanup, and confirmed-state boundary.
- Modify `addon/oig-proxy/device_id.py`: persist only normalized identities supplied by the CRC-valid connection callback.
- Modify `addon/oig-proxy/main.py`: store recovery, coordinator/sweeper lifecycle, exact handler reconciliation, startup warnings, and orderly shutdown.
- Modify `addon/oig-proxy/sensor/processor.py`: publish and cache a setting only after committed confirmation.
- Modify `addon/oig-proxy/telemetry/settings_audit.py`: stateless projection of committed transitions using persisted identities and exact wire fields.
- Modify `addon/oig-proxy/telemetry/collector.py`: serialize the extended audit record without owning lifecycle truth.
- Modify `addon/oig-proxy/capture/frame_capture.py`: nullable command/audit/attempt link columns and identical ONLINE/OFFLINE attempt capture.
- Modify `addon/oig-proxy/config.py`, `addon/oig-proxy/config.json`, and `addon/oig-proxy/run`: gate, lifecycle limits, compatibility precedence, internal paths, and version `2.2.0`.

### New and reorganized tests

- Create `tests/v2/egress_guard.py` and `tests/v2/test_egress_guard.py`: session guard and deterministic evidence artifact.
- Create `tests/v2/test_control_config.py`, `tests/v2/test_settings_constraints.py`, `tests/v2/test_twin_store.py`, `tests/v2/test_setting_dialog.py`, `tests/v2/test_setting_confirmation.py`, and `tests/v2/test_release_evidence.py`.
- Create `tests/v2/test_proxy/test_writer.py`, `tests/v2/test_proxy/test_setting_dialog_online.py`, `tests/v2/test_proxy/test_setting_dialog_offline.py`, and `tests/v2/test_proxy/test_setting_streams.py`.
- Create `tests/v2/e2e/__init__.py`, `tests/v2/e2e/conftest.py`, `tests/v2/e2e/fakes.py`, and `tests/v2/e2e/test_local_setting_transaction.py`.
- Modify focused existing tests under `tests/v2/test_ack_parser.py`, `test_setting_frame_builder.py`, `test_twin_delivery.py`, `test_twin_handler.py`, `test_integration.py`, `test_main_integration.py`, `test_frame_processor.py`, `test_settings_audit_contract.py`, `test_capture_modules.py`, `test_mqtt/test_client.py`, and `test_protocol/`.
- Modify `tests/v2/conftest.py` and `pytest.ini`: store/config factories, markers, and egress plugin registration.

### Verification, CI, and documentation

- Create `docs/superpowers/reports/2026-08-06-local-setting-transaction-hardening-traceability.md` before implementation.
- Create `ci/check_coverage.py` and `tests/v2/test_ci_coverage_gate.py` for independent statement/branch thresholds.
- Modify `.coveragerc`, `ci/ci.sh`, `.github/scripts/run_tests.sh`, `.github/scripts/run_security.sh`, `.github/workflows/ci.yml`, `.github/workflows/pylint.yml`, `.github/workflows/security-scan.yml`, `.gitleaks.toml`, and `.semgrep.yml` so gates fail closed and upload evidence.
- Modify `CHANGELOG.md`, `README.md`, `SECURITY.md`, `docs/v2/configuration.md`, `docs/v2/twin.md`, `docs/v2/architecture.md`, `docs/v2/proxy_modes.md`, `docs/CI_CD_OVERVIEW.md`, `docs/SECURITY_TESTING.md`, and `.github/workflows/README.md`.
- Create `docs/superpowers/reports/2026-08-06-local-setting-transaction-hardening-owasp.md` only after all verification commands pass.

---

### Task 1: Lock the SI-1 Through SI-15 Test Contract

**Files:**
- Create: `docs/superpowers/reports/2026-08-06-local-setting-transaction-hardening-traceability.md`
- Create: `tests/v2/test_release_evidence.py`

**Interfaces:**
- Consumes: Safety invariants 1 through 15 from the approved design.
- Produces: Stable test-node names implemented by Tasks 4 through 14 and validated by Task 15.

- [ ] **Step 1: Write the failing traceability contract tests**

Create `tests/v2/test_release_evidence.py` with path constants, a Markdown-row parser, and these tests:

```python
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
TRACE = ROOT / "docs/superpowers/reports/2026-08-06-local-setting-transaction-hardening-traceability.md"
ROW = re.compile(r"^\| SI-(\d+) \| `([^`]+)` \| `([^`]+)` \| `([^`]+)` \|$")


def _trace_rows() -> dict[int, tuple[str, str, str]]:
    rows: dict[int, tuple[str, str, str]] = {}
    for line in TRACE.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match:
            rows[int(match.group(1))] = (
                match.group(2), match.group(3), match.group(4)
            )
    return rows


def test_traceability_covers_si_1_through_si_15() -> None:
    assert set(_trace_rows()) == set(range(1, 16))


def test_traceability_uses_unit_integration_and_e2e_nodes() -> None:
    for unit, integration, e2e in _trace_rows().values():
        assert unit.startswith("tests/v2/")
        assert integration.startswith("tests/v2/")
        assert e2e.startswith("tests/v2/e2e/")
        assert "::test_" in unit
        assert "::test_" in integration
        assert "::test_e2e_" in e2e
```

- [ ] **Step 2: Run the contract to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_release_evidence.py::test_traceability_covers_si_1_through_si_15 -q
```

Expected: FAIL with `FileNotFoundError` because the traceability report does not exist.

- [ ] **Step 3: Create the exact traceability matrix**

Create the report with the design link, invariant text, owner task, and this stable node mapping:

```markdown
| Invariant | Unit node | Integration node | E2E node |
|---|---|---|---|
| SI-1 | `tests/v2/test_twin_delivery.py::test_claim_requires_correlated_cloud_terminal_end` | `tests/v2/test_proxy/test_setting_dialog_online.py::test_poll_reaches_cloud_and_queue_stays_pending_until_terminal_end` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_online_cloud_priority_then_local_batch` |
| SI-2 | `tests/v2/test_setting_dialog.py::test_cloud_setting_marks_cycle_cloud_owned_without_local_claim` | `tests/v2/test_proxy/test_setting_dialog_online.py::test_cloud_setting_and_box_ack_round_trip_byte_exact_before_local_batch` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_online_cloud_priority_then_local_batch` |
| SI-3 | `tests/v2/test_twin_delivery.py::test_ack_requires_active_session_and_dialog_owner` | `tests/v2/test_proxy/test_setting_dialog_online.py::test_wrong_session_ack_cannot_advance_local_command` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_foreign_session_cannot_advance_active_command` |
| SI-4 | `tests/v2/test_twin_delivery.py::test_ack_moves_to_awaiting_event_without_state_publication` | `tests/v2/test_proxy/test_setting_dialog_online.py::test_local_ack_is_suppressed_and_does_not_publish_confirmed_state` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_ack_is_delivery_only_until_exact_event` |
| SI-5 | `tests/v2/test_setting_confirmation.py::test_matcher_requires_exact_device_table_key_and_canonical_value` | `tests/v2/test_proxy/test_setting_dialog_online.py::test_only_exact_event_confirms_and_publishes_state` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_matching_event_confirms_and_nonmatching_event_does_not` |
| SI-6 | `tests/v2/test_twin_store.py::test_enqueue_after_attempt_creates_successor_without_mutating_predecessor` | `tests/v2/test_twin_delivery.py::test_rapid_same_key_updates_preserve_attempted_predecessor` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_rapid_same_key_updates_do_not_overwrite_attempted_command` |
| SI-7 | `tests/v2/test_twin_store.py::test_retry_preserves_stable_fields_and_refreshes_attempt_fields_only` | `tests/v2/test_twin_delivery.py::test_disconnect_requeues_same_wire_identity_for_next_dialogue` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_restart_retries_with_stable_identity` |
| SI-8 | `tests/v2/test_twin_store.py::test_attempt_limits_one_and_eight_and_nack_is_terminal` | `tests/v2/test_twin_delivery.py::test_timeout_stops_at_limit_and_nack_never_retries` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_retry_limit_and_terminal_nack` |
| SI-9 | `tests/v2/test_ack_parser.py::test_invalid_crc_cannot_bind_parse_ack_or_match_event` | `tests/v2/test_proxy/test_setting_dialog_online.py::test_invalid_crc_cannot_mutate_active_dialogue` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_invalid_crc_never_selects_advances_or_confirms` |
| SI-10 | `tests/v2/test_protocol/test_frame.py::test_stream_assembly_preserves_exact_raw_frames_and_remainder` | `tests/v2/test_proxy/test_setting_streams.py::test_disabled_or_empty_queue_is_byte_transparent_for_partial_and_coalesced_frames` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_partial_and_coalesced_frames_preserve_bytes_and_order` |
| SI-11 | `tests/v2/test_setting_dialog.py::test_local_delivery_trigger_accepts_only_isnewset` | `tests/v2/test_proxy/test_setting_dialog_offline.py::test_firmware_weather_and_unrelated_frames_never_claim` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_non_setting_polls_never_trigger_delivery` |
| SI-12 | `tests/v2/test_control_config.py::test_control_defaults_are_fail_closed` | `tests/v2/test_main_integration.py::test_startup_disabled_recovers_store_without_handler_or_local_write` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_disabled_control_has_no_subscription_discovery_or_write` |
| SI-13 | `tests/v2/test_twin_handler.py::test_retained_message_is_rejected_before_json_parse_and_enqueue` | `tests/v2/test_twin_handler.py::test_retained_control_creates_ingress_audit_only` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_retained_control_never_enters_local_batch` |
| SI-14 | `tests/v2/test_twin_handler.py::test_unknown_device_refuses_subscription_and_enqueue` | `tests/v2/test_main_integration.py::test_unknown_device_poll_and_control_cannot_claim` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_no_delivery_before_valid_device_identity` |
| SI-15 | `tests/v2/test_twin_store.py::test_every_transition_and_attempt_reuses_original_command_and_audit_ids` | `tests/v2/test_settings_audit_contract.py::test_write_outcomes_and_telemetry_reuse_persisted_identity` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_audit_identity_survives_all_write_outcomes` |
```

The report must also copy each invariant's full sentence, map it to its implementing task number, and state that node renames require an atomic matrix update.

- [ ] **Step 4: Run the traceability contract to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_release_evidence.py -q
```

Expected: PASS for both traceability tests; later OWASP/version tests do not exist yet.

- [ ] **Step 5: Commit the traceability contract**

```bash
git add docs/superpowers/reports/2026-08-06-local-setting-transaction-hardening-traceability.md tests/v2/test_release_evidence.py
git commit -m "test: define local-setting safety traceability"
```

---

### Task 2: Enforce Hermetic Local-Control Test Egress

**Files:**
- Create: `tests/v2/egress_guard.py`
- Create: `tests/v2/test_egress_guard.py`
- Create: `tests/v2/e2e/__init__.py`
- Modify: `tests/v2/conftest.py`
- Modify: `pytest.ini`

**Interfaces:**
- Consumes: `pytest` session hooks and Python `socket` APIs.
- Produces: `EgressGuard.validate_config(config: Config) -> None`, a `local_control` marker, and deterministic `reports/egress-guard.json` consumed by all later local-control tests and final verification.

- [ ] **Step 1: Write guard boundary tests**

Create tests that use an uninstalled `EgressGuard` directly so the guard can test itself without leaking traffic:

```python
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from egress_guard import EgressGuard, EgressViolation


def test_guard_blocks_dns_resolution(tmp_path: Path) -> None:
    guard = EgressGuard(tmp_path / "guard.json")
    with guard.installed(probe=True), pytest.raises(EgressViolation):
        socket.getaddrinfo("bridge.oigpower.cz", 5710)


def test_guard_blocks_non_loopback_tcp(tmp_path: Path) -> None:
    guard = EgressGuard(tmp_path / "guard.json")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with guard.installed(probe=True), pytest.raises(EgressViolation):
        sock.connect(("192.168.1.10", 5710))
    sock.close()


def test_guard_blocks_non_loopback_udp(tmp_path: Path) -> None:
    guard = EgressGuard(tmp_path / "guard.json")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    with guard.installed(probe=True), pytest.raises(EgressViolation):
        sock.sendto(b"x", ("8.8.8.8", 53))
    sock.close()


def test_guard_rejects_production_transport_config(tmp_path: Path) -> None:
    guard = EgressGuard(tmp_path / "guard.json")
    config = SimpleNamespace(
        proxy_host="127.0.0.1",
        cloud_host="bridge.oigpower.cz",
        mqtt_host="core-mosquitto",
        dns_upstream="8.8.8.8",
        telemetry_mqtt_broker="telemetry.muriel-cz.cz:1883",
        telemetry_enabled=True,
        twin_db_path="/data/twin_queue.db",
    )
    with pytest.raises(EgressViolation):
        guard.validate_config(config)


def test_guard_allows_numeric_loopback(tmp_path: Path) -> None:
    guard = EgressGuard(tmp_path / "guard.json")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with guard.installed(probe=True):
        client.connect(listener.getsockname())
    client.close()
    listener.close()


def test_guard_artifact_schema_is_deterministic(tmp_path: Path) -> None:
    report = tmp_path / "guard.json"
    guard = EgressGuard(report)
    guard.write_report(pytest_exit_status=0)
    assert list(json.loads(report.read_text()).keys()) == [
        "status", "policies", "self_probes", "blocked_violation_count",
        "allowed_loopback_attempt_count", "pytest_exit_status",
    ]
```

- [ ] **Step 2: Run guard tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_egress_guard.py -q
```

Expected: FAIL because `tests/v2/egress_guard.py` and its types do not exist.

- [ ] **Step 3: Implement the guard and session plugin**

Implement the core policy with numeric-loopback checks and reversible monkeypatches:

```python
class EgressViolation(AssertionError):
    pass


def _loopback_address(host: object) -> bool:
    if not isinstance(host, str):
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class EgressGuard:
    def validate_config(self, config: object) -> None:
        hosts = (
            config.proxy_host,
            config.cloud_host,
            config.mqtt_host,
            config.dns_upstream,
            str(config.telemetry_mqtt_broker).rsplit(":", 1)[0],
        )
        if not all(_loopback_address(host) for host in hosts):
            raise EgressViolation("local-control E2E config contains non-loopback host")
        if config.telemetry_enabled:
            raise EgressViolation("telemetry must be disabled in local-control E2E")
        if str(config.twin_db_path).startswith("/data/"):
            raise EgressViolation("E2E requires a temporary twin database")
```

The `installed()` context manager must patch and restore `getaddrinfo`, `gethostbyname`, `gethostbyname_ex`, `gethostbyaddr`, `getnameinfo`, `getfqdn`, `socket.connect`, `connect_ex`, `sendto`, and `sendmsg`; permit only numeric `127.0.0.0/8`, `::1/128`, and `AF_UNIX`; and record probe versus unexpected violations separately. Register a pytest plugin that creates one session guard, runs deterministic startup self-probes for blocked DNS, blocked TCP, blocked UDP, allowed IPv4 loopback, and allowed IPv6 loopback, then installs that guard for every `@pytest.mark.local_control` or `@pytest.mark.e2e` test. A failed self-probe or any unexpected block fails the session. At session finish, write the configured `LOCAL_CONTROL_EGRESS_REPORT` with all five self-probe results, the unexpected-block count, loopback attempts, and pytest exit status.

Add to `pytest.ini`:

```ini
markers =
    enable_socket: uses only local loopback sockets
    local_control: exercises local-setting code under the egress guard
    e2e: runs the real proxy against loopback fake endpoints
```

- [ ] **Step 4: Run guard tests to verify green**

Run:

```bash
LOCAL_CONTROL_EGRESS_REPORT=reports/egress-guard.json .venv/bin/python -m pytest tests/v2/test_egress_guard.py -q
```

Expected: six tests PASS and `reports/egress-guard.json` records all five self-probes, zero unexpected violations, and status `pass`.

- [ ] **Step 5: Commit the hermetic test boundary**

```bash
git add pytest.ini tests/v2/conftest.py tests/v2/egress_guard.py tests/v2/test_egress_guard.py tests/v2/e2e/__init__.py
git commit -m "test: enforce hermetic local-setting verification"
```

---

### Task 3: Add Fail-Closed Control Configuration

**Files:**
- Modify: `addon/oig-proxy/config.py`
- Modify: `addon/oig-proxy/config.json`
- Modify: `addon/oig-proxy/run`
- Create: `tests/v2/test_control_config.py`
- Modify: `tests/v2/test_addon_dns_config.py`
- Modify: `tests/v2/conftest.py`

**Interfaces:**
- Consumes: Existing `CLOUD_ACK_TIMEOUT` compatibility input.
- Produces: `Config.control_mqtt_enabled: bool`, `control_ack_timeout_s: float`, `control_event_timeout_s: float`, `control_command_ttl_s: float`, `control_max_attempts: int`, `twin_db_path: str`, `cloud_dialog_timeout_s: float`, and `startup_warnings: tuple[str, ...]`.

- [ ] **Step 1: Write config precedence and bound tests**

Create `tests/v2/test_control_config.py` with isolated environment setup and these assertions:

```python
import pytest

from config import Config


def test_control_defaults_are_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CONTROL_MQTT_ENABLED", "CONTROL_ACK_TIMEOUT_S",
        "CONTROL_EVENT_TIMEOUT_S", "CONTROL_COMMAND_TTL_S",
        "CONTROL_MAX_ATTEMPTS", "CLOUD_ACK_TIMEOUT", "TWIN_DB_PATH",
        "CLOUD_DIALOG_TIMEOUT_S",
    ):
        monkeypatch.delenv(name, raising=False)
    config = Config()
    assert config.control_mqtt_enabled is False
    assert config.control_ack_timeout_s == 30.0
    assert config.control_event_timeout_s == 300.0
    assert config.control_command_ttl_s == 900.0
    assert config.control_max_attempts == 8
    assert config.twin_db_path == "/data/twin_queue.db"
    assert config.cloud_dialog_timeout_s == 30.0
    assert config.startup_warnings == ()


@pytest.mark.parametrize("raw", ["", "yes", "on", "2", "invalid"])
def test_invalid_control_gate_is_disabled_with_one_warning(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("CONTROL_MQTT_ENABLED", raw)
    config = Config()
    assert config.control_mqtt_enabled is False
    assert config.startup_warnings == (
        "invalid CONTROL_MQTT_ENABLED; local control remains disabled",
    )


@pytest.mark.parametrize(
    ("new", "legacy", "expected", "warning"),
    [
        ("12", "99", 12.0, None),
        (None, "44", 44.0, "CLOUD_ACK_TIMEOUT is deprecated"),
        ("bad", "44", 30.0, "invalid CONTROL_ACK_TIMEOUT_S"),
        (None, "bad", 30.0, "invalid CLOUD_ACK_TIMEOUT"),
        ("0", None, 1.0, None),
    ],
)
def test_control_ack_timeout_precedence(
    monkeypatch: pytest.MonkeyPatch,
    new: str | None,
    legacy: str | None,
    expected: float,
    warning: str | None,
) -> None:
    for name, value in (("CONTROL_ACK_TIMEOUT_S", new), ("CLOUD_ACK_TIMEOUT", legacy)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    config = Config()
    assert config.control_ack_timeout_s == expected
    assert (warning is None) == (config.startup_warnings == ())
    if warning is not None:
        assert warning in config.startup_warnings[0]


@pytest.mark.parametrize(("raw", "expected"), [("0", 1), ("1", 1), ("8", 8), ("9", 8), ("2.5", 8), ("x", 8)])
def test_control_max_attempts_bounds(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
) -> None:
    monkeypatch.setenv("CONTROL_MAX_ATTEMPTS", raw)
    assert Config().control_max_attempts == expected
```

Extend `test_addon_dns_config.py` to assert version `2.2.0`, the four new option defaults and schemas, and literal `run` exports with shell fallbacks `30`, `300`, `900`, and `8`.

- [ ] **Step 2: Run config tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_control_config.py tests/v2/test_addon_dns_config.py -q
```

Expected: FAIL because the runtime fields, add-on options, and bridge exports do not exist and the version is `2.1.1`.

- [ ] **Step 3: Implement typed parsing and compatibility precedence**

Add explicit helpers that never throw on lifecycle input. Parse the hard gate separately: only normalized `"1"` and `"true"` enable it; normalized `"0"` and `"false"` disable it; absence disables it; every other value disables it and appends exactly one bounded startup warning.

```python
def _bounded_float(
    name: str,
    default: float,
    minimum: float,
    warnings: list[str],
) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        warnings.append(f"invalid {name}; using {default:g}")
        return default
    if not math.isfinite(parsed):
        warnings.append(f"invalid {name}; using {default:g}")
        return default
    return max(minimum, parsed)


def _ack_timeout(warnings: list[str]) -> float:
    if "CONTROL_ACK_TIMEOUT_S" in os.environ:
        return _bounded_float("CONTROL_ACK_TIMEOUT_S", 30.0, 1.0, warnings)
    if "CLOUD_ACK_TIMEOUT" not in os.environ:
        return 30.0
    value = _bounded_float("CLOUD_ACK_TIMEOUT", 30.0, 1.0, warnings)
    if not warnings or not warnings[-1].startswith("invalid CLOUD_ACK_TIMEOUT"):
        warnings.append("CLOUD_ACK_TIMEOUT is deprecated for local control")
    return value
```

Parse `control_max_attempts` only with base-10 `int`; a fractional or nonnumeric value returns `8`; clamp valid integers to `1..8`. Reject non-finite lifecycle floats exactly like nonnumeric input. Parse `control_event_timeout_s`, `control_command_ttl_s`, and internal `cloud_dialog_timeout_s` with a minimum of one second. Store warnings once as `tuple(warnings)`. Remove the existing direct `float(CLOUD_ACK_TIMEOUT)` parse so malformed legacy input cannot abort `Config()`. Retain `cloud_ack_timeout` as a deprecated 2.2.x compatibility attribute assigned from the already-sanitized `control_ack_timeout_s`; no runtime path may use it for events or cloud-cycle timeouts.

In `config.json`, keep `control_mqtt_enabled=false`, add:

```json
"control_ack_timeout_s": 30.0,
"control_event_timeout_s": 300.0,
"control_command_ttl_s": 900.0,
"control_max_attempts": 8
```

with schemas `float`, `float`, `float`, and `int`, and set version `2.2.0`. In `run`, always export `CONTROL_ACK_TIMEOUT_S`, `CONTROL_EVENT_TIMEOUT_S`, `CONTROL_COMMAND_TTL_S`, and `CONTROL_MAX_ATTEMPTS` from those options using the exact shipped fallbacks.

- [ ] **Step 4: Run config tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_control_config.py tests/v2/test_addon_dns_config.py -q
```

Expected: PASS, including the full precedence truth table and add-on bridge contract.

- [ ] **Step 5: Commit configuration**

```bash
git add addon/oig-proxy/config.py addon/oig-proxy/config.json addon/oig-proxy/run tests/v2/conftest.py tests/v2/test_control_config.py tests/v2/test_addon_dns_config.py
git commit -m "feat: configure local-setting transaction limits"
```

---

### Task 4: Canonicalize Values and Render One Safe Setting Frame

**Files:**
- Modify: `addon/oig-proxy/settings_constraints.py`
- Modify: `addon/oig-proxy/protocol/frames.py`
- Modify: `addon/oig-proxy/proxy/server.py`
- Create: `tests/v2/test_settings_constraints.py`
- Modify: `tests/v2/test_setting_frame_builder.py`
- Modify: `tests/v2/test_twin_delivery.py`

**Interfaces:**
- Consumes: Existing `CONTROL_WRITE_WHITELIST` and CRC-16/MODBUS implementation.
- Produces: `SettingConstraint`, `SettingValueResult`, `canonical_decimal_text()`, `validate_setting_value()`, `is_xml_1_0_text()`, `escape_xml_text()`, `RenderedSettingFrame`, and deterministic `build_setting_frame()` for Tasks 7 through 10.

- [ ] **Step 1: Write Decimal validation tests**

Create `tests/v2/test_settings_constraints.py` with explicit alias, finite, range, step, and canonicalization cases:

```python
from decimal import Decimal

import pytest

from settings_constraints import (
    SettingConstraint,
    canonical_decimal_text,
    validate_constraint_value,
    validate_setting_value,
)


def test_rejects_boolean_without_explicit_alias() -> None:
    result = validate_constraint_value(
        True,
        SettingConstraint(Decimal("0"), Decimal("2"), Decimal("1"), True),
    )
    assert result.accepted is False
    assert result.reason == "boolean alias is not allowed"


@pytest.mark.parametrize(("raw", "expected"), [(True, "1"), (False, "0"), ("on", "1"), ("off", "0")])
def test_accepts_declared_boolean_aliases(raw: object, expected: str) -> None:
    constraint = SettingConstraint(
        Decimal("0"), Decimal("1"), Decimal("1"), True, True
    )
    assert validate_constraint_value(raw, constraint).value_text == expected


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", float("nan"), float("inf")])
def test_rejects_nan_and_infinity(raw: object) -> None:
    result = validate_setting_value("tbl_box_prms", "MODE", raw)
    assert result == type(result)(False, None, "value must be finite")


@pytest.mark.parametrize(("raw", "accepted"), [("0", True), ("10000", True), ("-100", False), ("10001", False), ("50", False), ("100", True)])
def test_enforces_range_and_step(raw: str, accepted: bool) -> None:
    assert validate_setting_value("tbl_boiler_prms", "P_SET", raw).accepted is accepted


def test_canonicalizes_decimal_without_exponent() -> None:
    assert canonical_decimal_text(Decimal("1.2300E+3")) == "1230"
    assert canonical_decimal_text(Decimal("-0.000")) == "0"
```

- [ ] **Step 2: Write the exact serializer contract**

Replace presence-only Setting tests with one frozen wire assertion and an independent bitwise CRC helper local to the test:

```python
def independent_modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def test_build_setting_frame_matches_exact_golden_bytes() -> None:
    rendered = build_setting_frame(
        device_id="123456",
        table_name="tbl_box_prms",
        item_name="MODE",
        value_text="2",
        wire_id=14000001,
        wire_id_set=1786000000,
        wire_dt="06.08.2026 10:11:12",
        tsec_text="2026-08-06 08:11:13",
        ver_text="00042",
    )
    expected = (
        b"<Frame><ID>14000001</ID><ID_Device>123456</ID_Device>"
        b"<ID_Set>1786000000</ID_Set><ID_SubD>0</ID_SubD>"
        b"<DT>06.08.2026 10:11:12</DT><NewValue>2</NewValue>"
        b"<Confirm>New</Confirm><TblName>tbl_box_prms</TblName>"
        b"<TblItem>MODE</TblItem><ID_Server>9</ID_Server>"
        b"<mytimediff>0</mytimediff><Reason>Setting</Reason>"
        b"<TSec>2026-08-06 08:11:13</TSec><ver>00042</ver>"
        b"<CRC>63234</CRC></Frame>\r\n"
    )
    assert rendered.wire_frame == expected
    inner = expected[len(b"<Frame>"):expected.index(b"<CRC>")]
    assert independent_modbus_crc(inner) == 63234
    assert rendered.crc_text == "63234"
    assert rendered.wire_length == len(expected)


def test_build_setting_frame_escapes_dynamic_text() -> None:
    rendered = build_setting_frame(
        device_id="A&amp;B",
        table_name="tbl_box_prms",
        item_name="MODE",
        value_text="1 < 2 & 3 > 2",
        wire_id=1,
        wire_id_set=2,
        wire_dt="06.08.2026 10:11:12",
        tsec_text="2026-08-06 08:11:13",
        ver_text="00001",
    )
    assert b"<ID_Device>A&amp;amp;B</ID_Device>" in rendered.wire_frame
    assert b"<NewValue>1 &lt; 2 &amp; 3 &gt; 2</NewValue>" in rendered.wire_frame
```

Add a forbidden XML 1.0 code-point assertion using `"bad\x00value"`; assert `ver_text` is exactly five decimal digits and an integer in `0..65535`; explicitly accept `"65535"` and reject `"65536"` and `"99999"`.

- [ ] **Step 3: Run value and serializer tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_settings_constraints.py tests/v2/test_setting_frame_builder.py tests/v2/test_twin_delivery.py::test_build_setting_frame_format -q
```

Expected: FAIL because Decimal results and deterministic renderer fields do not exist; the current validator accepts off-step values.

- [ ] **Step 4: Implement Decimal validation**

Replace float storage and tuple results with these immutable contracts:

```python
@dataclass(frozen=True, slots=True)
class SettingConstraint:
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    step: Decimal | None = None
    integer_only: bool = False
    boolean_aliases: bool = False


@dataclass(frozen=True, slots=True)
class SettingValueResult:
    accepted: bool
    value_text: str | None
    reason: str


def canonical_decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("value must be finite")
    if value == 0:
        return "0"
    fixed = format(value, "f")
    return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed


def validate_constraint_value(
    value: object,
    constraint: SettingConstraint,
) -> SettingValueResult:
    # Resolve aliases only when constraint.boolean_aliases is true.
    # Convert accepted int/float/str/Decimal input through Decimal text.
    # Reject non-finite, enforce integer, inclusive bounds, then exact Decimal step.
    origin = constraint.min_value if constraint.min_value is not None else Decimal(0)
    if constraint.step is not None and (parsed - origin) % constraint.step != 0:
        return SettingValueResult(False, None, "value is not aligned to step")
    return SettingValueResult(True, canonical_decimal_text(parsed), "")
```

Implement every existing constraint with quoted `Decimal` values. Set `boolean_aliases=True` only for current exact zero/one switch targets; `PROXY_MODE`, `MODE`, and multi-value selectors reject booleans and `on/off` aliases. `validate_setting_value(table, key, value)` must reject targets without both an allowlist entry and a concrete constraint.

- [ ] **Step 5: Implement the sole Setting serializer**

Use this exact public contract and keep time/ID/version generation outside it:

```python
@dataclass(frozen=True, slots=True)
class RenderedSettingFrame:
    wire_frame: bytes
    crc_text: str
    wire_length: int


def build_setting_frame(
    *,
    device_id: str,
    table_name: str,
    item_name: str,
    value_text: str,
    wire_id: int,
    wire_id_set: int,
    wire_dt: str,
    tsec_text: str,
    ver_text: str,
) -> RenderedSettingFrame:
    dynamic = (device_id, table_name, item_name, value_text, wire_dt, tsec_text)
    if not all(is_xml_1_0_text(text) for text in dynamic):
        raise ValueError("dynamic Setting text is not valid XML 1.0")
    if not re.fullmatch(r"[0-9]{5}", ver_text) or int(ver_text) > 65535:
        raise ValueError("ver_text must be a zero-padded uint16 decimal")
    inner = (
        f"<ID>{wire_id}</ID><ID_Device>{escape_xml_text(device_id)}</ID_Device>"
        f"<ID_Set>{wire_id_set}</ID_Set><ID_SubD>0</ID_SubD>"
        f"<DT>{escape_xml_text(wire_dt)}</DT>"
        f"<NewValue>{escape_xml_text(value_text)}</NewValue>"
        f"<Confirm>New</Confirm><TblName>{escape_xml_text(table_name)}</TblName>"
        f"<TblItem>{escape_xml_text(item_name)}</TblItem><ID_Server>9</ID_Server>"
        f"<mytimediff>0</mytimediff><Reason>Setting</Reason>"
        f"<TSec>{escape_xml_text(tsec_text)}</TSec><ver>{ver_text}</ver>"
    ).encode("utf-8")
    crc_text = f"{crc16_modbus(inner):05d}"
    wire = b"<Frame>" + inner + b"<CRC>" + crc_text.encode("ascii") + b"</CRC></Frame>\r\n"
    return RenderedSettingFrame(wire, crc_text, len(wire))
```

Use `html.escape(text, quote=True)` plus an XML 1.0 character predicate. Do not validate table/item as XML tag names here because they remain text node values; the allowlist is the authorization gate.

Mechanically update the existing pre-cutover ONLINE caller in `proxy/server.py` to supply all explicit fields through its current ID/time/version helpers so the module remains importable. Do not change its routing semantics in this task; Task 10 replaces that branch. The legacy OFFLINE duplicate remains isolated until Task 11 and must not be called by new code.

- [ ] **Step 6: Run value and serializer tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_settings_constraints.py tests/v2/test_setting_frame_builder.py tests/v2/test_twin_delivery.py::test_build_setting_frame_format -q
```

Expected: PASS with literal CRC `63234`, exact child order, one final CRC, and CRLF.

- [ ] **Step 7: Commit the canonical value/wire boundary**

```bash
git add addon/oig-proxy/settings_constraints.py addon/oig-proxy/protocol/frames.py addon/oig-proxy/proxy/server.py tests/v2/test_settings_constraints.py tests/v2/test_setting_frame_builder.py tests/v2/test_twin_delivery.py
git commit -m "feat: validate and serialize local setting values"
```

---

### Task 5: Validate, Timestamp, and Classify Inbound Frames

**Files:**
- Modify: `addon/oig-proxy/protocol/crc.py`
- Modify: `addon/oig-proxy/protocol/frame.py`
- Modify: `addon/oig-proxy/protocol/parser.py`
- Modify: `addon/oig-proxy/twin/ack_parser.py`
- Modify: `tests/v2/test_protocol/test_crc.py`
- Modify: `tests/v2/test_protocol/test_frame.py`
- Modify: `tests/v2/test_protocol/test_parser.py`
- Modify: `tests/v2/test_ack_parser.py`

**Interfaces:**
- Consumes: `crc16_modbus(data: bytes) -> int` and complete TCP read chunks.
- Produces: `FrameDirection`, `AssembledFrame`, `FrameStreamAssembler`, `ValidatedFrame`, `FrameValidation`, `FrameMetadata`, `SettingResponse`, and `SettingEvent` for Tasks 7 through 10.

- [ ] **Step 1: Write bounded stream and exact CRC tests**

Add tests for split/coalesced terminators, exact size boundaries, EOF, and CRC shape:

```python
def test_stream_assembly_preserves_exact_raw_frames_and_remainder() -> None:
    first = valid_frame(b"<Result>IsNewSet</Result>")
    second = valid_frame(b"<Result>ACK</Result><Reason>Setting</Reason>")
    assembler = FrameStreamAssembler(max_frame_bytes=1_048_576)
    assert assembler.feed(first[:-1], received_at_ms=10) == ()
    frames = assembler.feed(first[-1:] + second, received_at_ms=11)
    assert tuple(frame.raw for frame in frames) == (first, second)
    assert tuple(frame.received_at_ms for frame in frames) == (11, 11)


def test_stream_assembler_allows_exact_limit_and_rejects_next_byte() -> None:
    exact = b"<Frame>" + b"x" * (64 - len(b"<Frame></Frame>\r\n")) + b"</Frame>\r\n"
    assert FrameStreamAssembler(max_frame_bytes=64).feed(exact, received_at_ms=1)[0].raw == exact
    with pytest.raises(FrameStreamError, match="buffer_overflow"):
        FrameStreamAssembler(max_frame_bytes=63).feed(exact, received_at_ms=1)


def test_validate_crc_tag_requires_one_final_crc5() -> None:
    assert validate_crc_tag(b"<A>1</A><CRC>1234</CRC>").error is CrcError.MALFORMED
    assert validate_crc_tag(b"<CRC>00000</CRC><A>1</A>").error is CrcError.NOT_FINAL
    assert validate_crc_tag(b"<A>1</A><CRC>00000</CRC><CRC>00000</CRC>").error is CrcError.DUPLICATE
```

Also assert exact `<Frame>` prefix, exact `</Frame>\r\n`, wait states for split `\r\n`, rejection of LF-only/CR-only endings, raw preservation after CRC mismatch, and `finish()` raising `EOF_PARTIAL` when bytes remain.

- [ ] **Step 2: Write typed response/event parser tests**

Use only `ValidatedFrame` fixtures and explicit direction:

```python
def test_parse_setting_ack_returns_reason_rdt_and_exact_sha256(validated_ack: ValidatedFrame) -> None:
    response = parse_setting_response(validated_ack, direction=FrameDirection.BOX_TO_PROXY)
    assert response == SettingResponse(
        result="ACK",
        reason="Setting",
        rdt_text="06.08.2026 10:12:00",
        fingerprint=hashlib.sha256(validated_ack.raw).hexdigest(),
    )


def test_parse_setting_response_rejects_cloud_direction(validated_ack: ValidatedFrame) -> None:
    assert parse_setting_response(validated_ack, direction=FrameDirection.CLOUD_TO_PROXY) is None


def test_parse_setting_event_returns_strict_old_and_new_values(validated_event: ValidatedFrame) -> None:
    event = parse_setting_event(validated_event, direction=FrameDirection.BOX_TO_PROXY)
    assert event is not None
    assert (event.table_name, event.item_name) == ("tbl_box_prms", "MODE")
    assert (event.old_value_text, event.new_value_text) == ("1", "2")
    assert event.evidence_id == derive_event_evidence_id(
        event.device_id, event.event_id_set, event.device_dt, event.content_text
    )


def test_invalid_crc_cannot_bind_parse_ack_or_match_event(raw_ack: bytes) -> None:
    damaged = AssembledFrame(raw_ack.replace(b"Setting", b"SettinX"), 1)
    validation = validate_frame(damaged)
    assert validation.validated is None
    assert validation.error is FrameValidationError.CRC_MISMATCH
```

Add cases for NACK diagnostic reason `WC`, missing/duplicate routing fields, unanchored event content, absent device/ID_Set/DT, wrong event type, nested routing fields, DTD/entity declarations, and evidence-ID changes when any hash input changes.

- [ ] **Step 3: Run protocol/evidence tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_protocol/test_crc.py tests/v2/test_protocol/test_frame.py tests/v2/test_protocol/test_parser.py tests/v2/test_ack_parser.py -q
```

Expected: FAIL because inbound CRC validation, bounded assembly, typed metadata, and immutable evidence do not exist.

- [ ] **Step 4: Implement exact CRC and stream contracts**

Add these types and signatures:

```python
FRAME_TERMINATOR = b"</Frame>\r\n"
MAX_FRAME_BYTES = 1_048_576


class FrameDirection(str, Enum):
    BOX_TO_PROXY = "box_to_proxy"
    CLOUD_TO_PROXY = "cloud_to_proxy"


class StreamErrorCode(str, Enum):
    INVALID_PREFIX = "invalid_prefix"
    FORBIDDEN_TERMINATOR = "forbidden_terminator"
    BUFFER_OVERFLOW = "buffer_overflow"
    EOF_PARTIAL = "eof_partial"


class FrameStreamError(ValueError):
    def __init__(self, code: StreamErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class CrcError(str, Enum):
    MISSING = "missing"
    MALFORMED = "malformed"
    DUPLICATE = "duplicate"
    NOT_FINAL = "not_final"
    MISMATCH = "mismatch"


class FrameValidationError(str, Enum):
    INVALID_ENVELOPE = "invalid_envelope"
    INVALID_XML = "invalid_xml"
    MISSING_CRC = "missing_crc"
    MALFORMED_CRC = "malformed_crc"
    DUPLICATE_CRC = "duplicate_crc"
    CRC_NOT_FINAL = "crc_not_final"
    CRC_MISMATCH = "crc_mismatch"


@dataclass(frozen=True, slots=True)
class AssembledFrame:
    raw: bytes
    received_at_ms: int


@dataclass(frozen=True, slots=True)
class ValidatedFrame:
    raw: bytes
    received_at_ms: int
    inner_without_crc: bytes
    transmitted_crc: int
    computed_crc: int


@dataclass(frozen=True, slots=True)
class FrameValidation:
    frame: AssembledFrame
    validated: ValidatedFrame | None
    error: FrameValidationError | None


@dataclass(frozen=True, slots=True)
class CrcValidation:
    payload_without_crc: bytes | None
    transmitted: int | None
    computed: int | None
    error: CrcError | None

    @property
    def valid(self) -> bool:
        return self.error is None


class FrameStreamAssembler:
    def __init__(self, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> None: ...
    def feed(self, chunk: bytes, *, received_at_ms: int) -> tuple[AssembledFrame, ...]: ...
    def finish(self) -> None: ...
    def reset(self) -> None: ...
```

`FrameStreamAssembler.feed(chunk, received_at_ms=...)` must accumulate until exact CRLF termination, return all complete raw frames, and reject junk prefixes or overflow. `validate_crc_tag()` must accept exactly one final `<CRC>[0-9]{5}</CRC>` and calculate over all exact preceding inner bytes. `validate_frame()` must preserve the original raw bytes even on error. Keep compatibility `parse_frame(bytes)` only as a validated wrapper, then remove `extract_frame_from_buffer()` after Task 10 migrates its last caller.

- [ ] **Step 5: Implement strict routing metadata and evidence**

Define the routing and evidence contracts exactly once:

```python
@dataclass(frozen=True, slots=True)
class FrameMetadata:
    result: str | None
    table_name: str | None
    device_id: str | None
    reason: str | None
    todo: str | None
    rdt: str | None
    message_id: int | None
    id_set: int | None
    item_name: str | None
    new_value: str | None
    event_type: str | None
    content: str | None

    @property
    def is_isnewset(self) -> bool:
        return self.result == "IsNewSet"


@dataclass(frozen=True, slots=True)
class SettingResponse:
    result: Literal["ACK", "NACK"]
    reason: str | None
    rdt_text: str | None
    fingerprint: str


@dataclass(frozen=True, slots=True)
class SettingEvent:
    evidence_id: str
    device_id: str
    event_id_set: int
    device_dt: str
    content_text: str
    table_name: str
    item_name: str
    old_value_text: str
    new_value_text: str
```

Before `xml.etree.ElementTree` parsing, reject `<!DOCTYPE` and `<!ENTITY` case-insensitively so no declaration can influence local state. Extract routing values from direct root children only; nested lookalikes do not count. Reject duplicate `Result`, `TblName`, `ID_Device`, `Reason`, `ToDo`, `Rdt`, `ID`, `ID_Set`, `TblItem`, `NewValue`, `Type`, or `Content`. Parse event content only with the fully anchored grammar `Remotely\s*:\s*([^/\[\]]+)\s*/\s*([^:\[\]]+)\s*:\s*\[([^\]]*)\]->\[([^\]]*)\]`. Derive fingerprints from exact raw frame bytes and event IDs from NUL-delimited UTF-8 `device_id`, decimal event `ID_Set`, device `DT`, and exact `Content`.

- [ ] **Step 6: Run protocol/evidence tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_protocol/test_crc.py tests/v2/test_protocol/test_frame.py tests/v2/test_protocol/test_parser.py tests/v2/test_ack_parser.py -q
```

Expected: PASS for exact boundaries, invalid CRC passivity, BOX-only evidence, and strict event grammar.

- [ ] **Step 7: Commit inbound protocol validation**

```bash
git add addon/oig-proxy/protocol/crc.py addon/oig-proxy/protocol/frame.py addon/oig-proxy/protocol/parser.py addon/oig-proxy/twin/ack_parser.py tests/v2/test_protocol tests/v2/test_ack_parser.py
git commit -m "feat: validate local-setting protocol evidence"
```

---

### Task 6: Create the Locked SQLite Source of Truth

**Files:**
- Create: `addon/oig-proxy/twin/store.py`
- Rewrite: `addon/oig-proxy/twin/state.py`
- Modify: `addon/oig-proxy/twin/__init__.py`
- Create: `tests/v2/test_twin_store.py`
- Rewrite: `tests/v2/test_twin_state.py`
- Modify: `tests/v2/conftest.py`

**Interfaces:**
- Consumes: `SettingEvent` from Task 5 and lifecycle limits from Task 3.
- Produces: immutable transaction records, `ControlPolicy`, schema v1, `TwinCommandStore.open()/close()/observe_device()`, process lock, and store exceptions consumed by Tasks 7 through 11.

- [ ] **Step 1: Write immutable state-contract tests**

Replace `TwinQueue` shape tests with exact enum and frozen-record contracts:

```python
from dataclasses import FrozenInstanceError

import pytest

from twin.state import AttemptWriteOutcome, CommandState, TERMINAL_STATES, TwinCommand


def test_command_state_values_and_terminal_set_are_exact() -> None:
    assert [state.value for state in CommandState] == [
        "pending", "retry_pending", "awaiting_ack", "awaiting_event",
        "confirmed", "incomplete", "failed", "expired", "superseded",
    ]
    assert TERMINAL_STATES == {
        CommandState.CONFIRMED, CommandState.INCOMPLETE, CommandState.FAILED,
        CommandState.EXPIRED, CommandState.SUPERSEDED,
    }


def test_write_outcome_values_are_exact() -> None:
    assert [outcome.value for outcome in AttemptWriteOutcome] == [
        "prepared", "started", "drained", "unknown", "failed"
    ]


def test_twin_command_snapshot_is_frozen(command: TwinCommand) -> None:
    with pytest.raises(FrozenInstanceError):
        command.state = CommandState.CONFIRMED  # type: ignore[misc]
```

- [ ] **Step 2: Write store bootstrap and preservation tests**

Add focused tests using `tmp_path` and a deterministic clock:

```python
def test_open_creates_schema_v1_and_repeated_open_is_idempotent(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    first = TwinCommandStore(path, policy=control_policy)
    first.open(now_ms=1000)
    assert first.schema_version == 1
    first.close()
    second = TwinCommandStore(path, policy=control_policy)
    second.open(now_ms=2000)
    assert second.schema_version == 1
    assert second.schema_created_at_ms == 1000
    second.close()


def test_open_enables_required_pragmas(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    store = TwinCommandStore(tmp_path / "twin.db", policy=control_policy)
    store.open(now_ms=1)
    assert store.pragma_snapshot() == PragmaSnapshot(
        journal_mode="wal", synchronous=2, foreign_keys=1, busy_timeout_ms=5000
    )
    store.close()


def test_open_holds_exclusive_process_lock_until_close(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    owner = TwinCommandStore(path, policy=control_policy)
    owner.open(now_ms=1)
    contender = TwinCommandStore(path, policy=control_policy)
    with pytest.raises(StoreLockError):
        contender.open(now_ms=2)
    owner.close()
    contender.open(now_ms=3)
    contender.close()


def test_corrupt_database_fails_closed_without_recreation(
    tmp_path: Path, control_policy: ControlPolicy
) -> None:
    path = tmp_path / "twin.db"
    original = b"not a sqlite database\x00keep me"
    path.write_bytes(original)
    store = TwinCommandStore(path, policy=control_policy)
    with pytest.raises(CorruptStoreError):
        store.open(now_ms=1)
    assert path.read_bytes() == original
```

Add tests for an unsupported schema version `2`, a nonempty SQLite file missing `schema_meta`, migration-statement rollback, idempotent `close()`, lock-file inode replacement detection, and no database delete/truncate/recreate on any failure.

- [ ] **Step 3: Run state and bootstrap tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_twin_state.py tests/v2/test_twin_store.py -q
```

Expected: FAIL because the immutable contracts, schema, lock, and store do not exist.

- [ ] **Step 4: Define all shared immutable transaction types**

In `twin/state.py`, define these exact enums beside the legacy symbols. Keep `TwinQueue` and its old record temporarily importable only so unrelated startup tests can remain green until the atomic runtime cutover in Task 13; no new store, coordinator, handler, or proxy code may import or mutate that compatibility queue.

```python
class CommandState(str, Enum):
    PENDING = "pending"
    RETRY_PENDING = "retry_pending"
    AWAITING_ACK = "awaiting_ack"
    AWAITING_EVENT = "awaiting_event"
    CONFIRMED = "confirmed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class AttemptWriteOutcome(str, Enum):
    PREPARED = "prepared"
    STARTED = "started"
    DRAINED = "drained"
    UNKNOWN = "unknown"
    FAILED = "failed"


class ClaimDisposition(str, Enum):
    PREPARED = "prepared"
    NO_ELIGIBLE = "no_eligible"
    ACTIVE_DELIVERY_ELSEWHERE = "active_delivery_elsewhere"
    CONTROL_DISABLED = "control_disabled"
    RENDER_FAILED = "render_failed"


class IngressDisposition(str, Enum):
    ACCEPTED_COMMAND = "accepted_command"
    ACCEPTED_PROXY_CONTROL = "accepted_proxy_control"
    REJECTED_DISABLED = "rejected_disabled"
    REJECTED_RETAINED = "rejected_retained"
    REJECTED_TOPIC = "rejected_topic"
    REJECTED_UNKNOWN_DEVICE = "rejected_unknown_device"
    REJECTED_DEVICE_MISMATCH = "rejected_device_mismatch"
    REJECTED_OVERSIZE = "rejected_oversize"
    REJECTED_UTF8 = "rejected_utf8"
    REJECTED_JSON = "rejected_json"
    REJECTED_SCHEMA = "rejected_schema"
    REJECTED_NOT_ALLOWED = "rejected_not_allowed"
    REJECTED_VALUE = "rejected_value"
    REJECTED_XML = "rejected_xml"
    REJECTED_STORE = "rejected_store"
```

Define frozen, slotted `ControlPolicy`, `TwinCommand`, `CommandAttempt`, `CommandTransition`, `SettingEventReceipt`, `ControlIngress`, `DeviceState`, `PragmaSnapshot`, `RecoveryReport`, and `StoreStatus`. `TwinCommand` must expose every `commands` column from the approved design using `int | None`, `str | None`, and `bytes | None` exactly; `CommandAttempt`, `CommandTransition`, and `SettingEventReceipt` must expose every column in their respective schema tables. Keep `TERMINAL_STATES` as an immutable `frozenset`.

Use these renderer and result contracts:

```python
@dataclass(frozen=True, slots=True)
class AttemptRenderContext:
    command: TwinCommand
    attempt_number: int
    prepared_at_ms: int
    wire_id: int
    wire_id_set: int
    wire_dt: str
    used_ver_texts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RenderedAttempt:
    tsec_text: str
    ver_text: str
    crc_text: str
    wire_frame: bytes


AttemptRenderer = Callable[[AttemptRenderContext], RenderedAttempt]
```

- [ ] **Step 5: Implement schema v1 and process locking**

Create `TwinCommandStore` with `isolation_level=None`, `check_same_thread=False`, one `threading.RLock`, and these exceptions. Expose read-only `schema_version: int`, `schema_created_at_ms: int`, `policy: ControlPolicy`, and `is_open: bool` properties; do not expose an arbitrary-SQL test helper. The store has no adapter to the compatibility queue and never imports it:

```python
class TwinStoreError(RuntimeError):
    pass


class StoreLockError(TwinStoreError):
    pass


class MigrationError(TwinStoreError):
    pass


class UnsupportedSchemaError(MigrationError):
    pass


class CorruptStoreError(TwinStoreError):
    pass


class StaleAttemptError(TwinStoreError):
    pass


class StoreRecordNotFound(LookupError):
    pass
```

Acquire `Path(f"{db_path}.lock")` through `os.open(..., os.O_CREAT | os.O_RDWR, 0o600)` and `fcntl.flock(fd, LOCK_EX | LOCK_NB)` before opening SQLite. Record the lock file's `(st_dev, st_ino)` and expose `verify_health()` that checks the live descriptor, current path inode, `PRAGMA quick_check`, and pragma readback. Close SQLite before unlocking/closing the descriptor.

Create tables in this dependency order using individual `execute()` calls inside one `BEGIN IMMEDIATE`: `schema_meta`, `devices`, `commands`, `control_ingress_audit`, `command_attempts`, `command_transitions`, and `event_receipts`. Use these exact definitions (split them into one constant string per statement; do not use `executescript()` because it introduces implicit transaction boundaries):

```sql
CREATE TABLE schema_meta (
    schema_version INTEGER PRIMARY KEY CHECK (schema_version >= 1),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
);

CREATE TABLE devices (
    device_id TEXT PRIMARY KEY CHECK (length(device_id) BETWEEN 1 AND 128),
    first_seen_at_ms INTEGER NOT NULL CHECK (first_seen_at_ms >= 0),
    last_seen_at_ms INTEGER NOT NULL CHECK (last_seen_at_ms >= first_seen_at_ms),
    next_wire_id INTEGER NOT NULL CHECK (next_wire_id >= 0),
    next_wire_id_set INTEGER NOT NULL CHECK (next_wire_id_set >= 0)
);

CREATE TABLE commands (
    command_id TEXT PRIMARY KEY,
    audit_id TEXT NOT NULL UNIQUE,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    table_name TEXT NOT NULL CHECK (length(table_name) BETWEEN 1 AND 128),
    item_name TEXT NOT NULL CHECK (length(item_name) BETWEEN 1 AND 128),
    value_text TEXT NOT NULL CHECK (length(value_text) BETWEEN 1 AND 1024),
    raw_ingress_text TEXT NOT NULL CHECK (length(raw_ingress_text) <= 16384),
    state TEXT NOT NULL CHECK (state IN (
        'pending','retry_pending','awaiting_ack','awaiting_event',
        'confirmed','incomplete','failed','expired','superseded'
    )),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= created_at_ms),
    pending_expires_at_ms INTEGER NOT NULL CHECK (pending_expires_at_ms >= created_at_ms),
    wire_id INTEGER,
    wire_id_set INTEGER,
    wire_dt TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 8),
    active_session_id TEXT,
    ack_deadline_ms INTEGER,
    event_deadline_ms INTEGER,
    acked_at_ms INTEGER,
    ack_device_rdt TEXT,
    completed_at_ms INTEGER,
    predecessor_command_id TEXT REFERENCES commands(command_id),
    last_wire_frame BLOB CHECK (last_wire_frame IS NULL OR length(last_wire_frame) <= 1048576),
    last_error TEXT CHECK (last_error IS NULL OR length(last_error) <= 1024),
    UNIQUE (command_id, audit_id),
    CHECK (predecessor_command_id IS NULL OR predecessor_command_id <> command_id),
    CHECK ((wire_id IS NULL AND wire_id_set IS NULL AND wire_dt IS NULL)
        OR (wire_id IS NOT NULL AND wire_id_set IS NOT NULL AND wire_dt IS NOT NULL))
);

CREATE TABLE control_ingress_audit (
    ingress_id TEXT PRIMARY KEY,
    received_at_ms INTEGER NOT NULL CHECK (received_at_ms >= 0),
    topic TEXT NOT NULL CHECK (length(topic) <= 1024),
    topic_device_id TEXT CHECK (topic_device_id IS NULL OR length(topic_device_id) <= 128),
    retain INTEGER NOT NULL CHECK (retain IN (0, 1)),
    disposition TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(reason) <= 1024),
    raw_text TEXT NOT NULL CHECK (length(raw_text) <= 16384),
    command_id TEXT,
    audit_id TEXT,
    CHECK ((command_id IS NULL AND audit_id IS NULL)
        OR (command_id IS NOT NULL AND audit_id IS NOT NULL)),
    FOREIGN KEY (command_id, audit_id) REFERENCES commands(command_id, audit_id)
);

CREATE TABLE command_attempts (
    command_id TEXT NOT NULL REFERENCES commands(command_id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number BETWEEN 1 AND 8),
    session_id TEXT NOT NULL CHECK (length(session_id) BETWEEN 1 AND 128),
    prepared_at_ms INTEGER NOT NULL CHECK (prepared_at_ms >= 0),
    write_started_at_ms INTEGER,
    drain_completed_at_ms INTEGER,
    ack_deadline_ms INTEGER NOT NULL CHECK (ack_deadline_ms >= prepared_at_ms),
    tsec_text TEXT NOT NULL,
    ver_text TEXT NOT NULL CHECK (length(ver_text) = 5),
    crc_text TEXT NOT NULL CHECK (length(crc_text) = 5),
    wire_frame BLOB NOT NULL CHECK (length(wire_frame) <= 1048576),
    wire_length INTEGER NOT NULL CHECK (wire_length = length(wire_frame)),
    write_outcome TEXT NOT NULL CHECK (
        write_outcome IN ('prepared','started','drained','unknown','failed')
    ),
    write_error TEXT CHECK (write_error IS NULL OR length(write_error) <= 1024),
    response_fingerprint TEXT CHECK (
        response_fingerprint IS NULL OR length(response_fingerprint) = 64
    ),
    response_rdt TEXT,
    PRIMARY KEY (command_id, attempt_number)
);

CREATE TABLE command_transitions (
    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL,
    audit_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
    attempt_number INTEGER,
    session_id TEXT,
    reason TEXT NOT NULL CHECK (length(reason) <= 1024),
    error_text TEXT CHECK (error_text IS NULL OR length(error_text) <= 1024),
    wire_frame BLOB CHECK (wire_frame IS NULL OR length(wire_frame) <= 1048576),
    evidence_frame BLOB CHECK (evidence_frame IS NULL OR length(evidence_frame) <= 1048576),
    FOREIGN KEY (command_id, audit_id) REFERENCES commands(command_id, audit_id)
);

CREATE TABLE event_receipts (
    evidence_id TEXT PRIMARY KEY CHECK (length(evidence_id) = 64),
    received_at_ms INTEGER NOT NULL CHECK (received_at_ms >= 0),
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    event_id_set INTEGER NOT NULL CHECK (event_id_set >= 0),
    device_dt TEXT NOT NULL,
    table_name TEXT NOT NULL CHECK (length(table_name) BETWEEN 1 AND 128),
    item_name TEXT NOT NULL CHECK (length(item_name) BETWEEN 1 AND 128),
    old_value_text TEXT NOT NULL CHECK (length(old_value_text) <= 1024),
    new_value_text TEXT NOT NULL CHECK (length(new_value_text) <= 1024),
    evidence_frame BLOB NOT NULL CHECK (length(evidence_frame) <= 1048576),
    disposition TEXT NOT NULL CHECK (disposition IN ('confirmed','unmatched')),
    command_id TEXT REFERENCES commands(command_id),
    duplicate_count INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
    last_seen_at_ms INTEGER NOT NULL CHECK (last_seen_at_ms >= received_at_ms)
);
```

Permit transition self-edges for attempt milestones; terminal command states remain immutable through mutation code. Enforce these core checks directly in schema as shown:

```sql
CHECK (state IN ('pending','retry_pending','awaiting_ack','awaiting_event',
                 'confirmed','incomplete','failed','expired','superseded'))
CHECK (attempt_count BETWEEN 0 AND 8)
CHECK ((wire_id IS NULL AND wire_id_set IS NULL AND wire_dt IS NULL)
    OR (wire_id IS NOT NULL AND wire_id_set IS NOT NULL AND wire_dt IS NOT NULL))
CHECK (write_outcome IN ('prepared','started','drained','unknown','failed'))
CHECK (wire_length = length(wire_frame))
CHECK (disposition IN ('confirmed','unmatched'))
```

Create these exact indexes:

```sql
CREATE INDEX idx_commands_fifo
ON commands(device_id, state, created_at_ms, command_id);
CREATE INDEX idx_commands_event_match
ON commands(device_id, table_name, item_name, value_text, state, acked_at_ms);
CREATE INDEX idx_commands_predecessor ON commands(predecessor_command_id);
CREATE UNIQUE INDEX ux_commands_one_awaiting_ack_per_device
ON commands(device_id) WHERE state = 'awaiting_ack';
CREATE UNIQUE INDEX ux_commands_one_unsent_successor_per_target
ON commands(device_id, table_name, item_name)
WHERE state = 'pending' AND attempt_count = 0;
CREATE UNIQUE INDEX ux_event_receipts_one_confirmation_per_command
ON event_receipts(command_id) WHERE command_id IS NOT NULL;
```

Use composite `(command_id, audit_id)` foreign keys for ingress/transition identity, no cascade deletes, event `ID_Set` as its parsed decimal integer, and the parser-supplied SHA-256 `evidence_id`. Store migration authority only in `schema_meta`; reject schema versions above `1`; never downgrade. Set and verify WAL, FULL sync, foreign keys, and busy timeout before returning from `open()`.

- [ ] **Step 6: Implement device counter observation without a guessed seed**

Add:

```python
def observe_device(
    self,
    *,
    device_id: str,
    observed_at_ms: int,
    observed_wire_id: int,
    observed_wire_id_set: int,
) -> DeviceState:
    """Insert/update exact identity and advance both next counters past valid observations."""
```

Require non-empty normalized device ID and non-negative observed integers from a CRC-valid BOX frame. Insert initial counters as `observed + 1`; update existing counters with `max(current, observed + 1)`. Local delivery cannot claim until the active connection has supplied both valid observed fields, so no synthetic initial `ID` seed or wrap rule is introduced. SQLite integer overflow raises, disables control, and never wraps an identity.

- [ ] **Step 7: Run state and bootstrap tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_twin_state.py tests/v2/test_twin_store.py -q
```

Expected: PASS for schema v1, pragma readback, lock ownership, corruption preservation, migrations, immutable records, and observed counters.

- [ ] **Step 8: Commit the durable store foundation**

```bash
git add addon/oig-proxy/twin/state.py addon/oig-proxy/twin/store.py addon/oig-proxy/twin/__init__.py tests/v2/conftest.py tests/v2/test_twin_state.py tests/v2/test_twin_store.py
git commit -m "feat: create durable local-setting store"
```

---

### Task 7: Implement Atomic Enqueue, Attempts, Evidence, Deadlines, and Recovery

**Files:**
- Modify: `addon/oig-proxy/twin/state.py`
- Modify: `addon/oig-proxy/twin/store.py`
- Modify: `tests/v2/test_twin_store.py`
- Modify: `tests/v2/test_twin_state.py`
- Create: `tests/v2/test_setting_confirmation.py`
- Modify: `tests/v2/conftest.py`

**Interfaces:**
- Consumes: Task 6 schema/records, `AttemptRenderer`, Task 5 `SettingEvent`, and Task 4 canonical values.
- Produces: the complete synchronous `TwinCommandStore` mutation API and committed snapshots consumed through `asyncio.to_thread()` by Task 8.

- [ ] **Step 1: Write atomic ingress and ordering tests**

Add helpers that query the store only through public read methods, then add these exact cases:

```python
def test_enqueue_commits_ingress_command_and_transition_atomically(store: TwinCommandStore) -> None:
    store.observe_device(
        device_id="123", observed_at_ms=100, observed_wire_id=14000000,
        observed_wire_id_set=1786000000,
    )
    ingress = ControlIngress("ing-1", 110, "oig/123/control/set", "123", False, '{"value":2}')
    result = store.enqueue_command(
        ingress, device_id="123", table_name="tbl_box_prms",
        item_name="MODE", value_text="2",
    )
    assert result.command.state is CommandState.PENDING
    assert result.command.pending_expires_at_ms == 110 + store.policy.pending_ttl_ms
    assert store.read_ingress("ing-1").command_id == result.command.command_id
    transition = store.read_transitions(command_id=result.command.command_id)[0]
    assert (transition.from_state, transition.to_state, transition.reason) == (
        None, "pending", "accepted_ingress"
    )


def test_enqueue_after_attempt_creates_successor_without_mutating_predecessor(
    store: TwinCommandStore, deterministic_renderer: AttemptRenderer
) -> None:
    first = enqueue(store, value_text="1", received_at_ms=100)
    claimed = store.prepare_next_attempt(
        device_id="123", session_id="session-a", prepared_at_ms=200,
        render=deterministic_renderer,
    )
    second = enqueue(store, value_text="2", received_at_ms=300)
    assert store.read_command(first.command_id) == claimed.command
    assert second.predecessor_command_id == first.command_id
    assert second.state is CommandState.PENDING


def test_enqueue_replaces_only_unsent_successor(
    store: TwinCommandStore, deterministic_renderer: AttemptRenderer
) -> None:
    first = enqueue(store, value_text="1", received_at_ms=100)
    store.prepare_next_attempt(
        device_id="123", session_id="session-a", prepared_at_ms=200,
        render=deterministic_renderer,
    )
    second = enqueue(store, value_text="2", received_at_ms=300)
    third = enqueue(store, value_text="3", received_at_ms=400)
    assert store.read_command(first.command_id).state is CommandState.AWAITING_ACK
    assert store.read_command(second.command_id).state is CommandState.SUPERSEDED
    assert third.predecessor_command_id == first.command_id


def test_every_transition_and_attempt_reuses_original_command_and_audit_ids(
    store: TwinCommandStore, deterministic_renderer: AttemptRenderer
) -> None:
    original = enqueue(store, value_text="2", received_at_ms=100)
    prepared = store.prepare_next_attempt(
        device_id="123", session_id="session-a", prepared_at_ms=200,
        render=deterministic_renderer,
    )
    store.mark_write_started(
        command_id=original.command_id, attempt_number=1,
        session_id="session-a", started_at_ms=201,
    )
    store.mark_attempt_drained(
        command_id=original.command_id, attempt_number=1,
        session_id="session-a", drained_at_ms=202,
    )
    assert prepared.attempt is not None
    assert prepared.attempt.command_id == original.command_id
    assert {
        (row.command_id, row.audit_id)
        for row in store.read_transitions(command_id=original.command_id)
    } == {(original.command_id, original.audit_id)}
```

Also test FIFO tie-breaking by `(created_at_ms, command_id)`, distinct-device independence, predecessor blocking, and same-target/same-value blocking while an earlier command is `awaiting_event`.

- [ ] **Step 2: Write attempt identity and write-outcome tests**

Use a renderer that records `AttemptRenderContext` and returns a different `ver_text` per call:

```python
def test_prepare_commits_frame_attempt_and_deadline_atomically(
    store: TwinCommandStore, deterministic_renderer: RecordingRenderer
) -> None:
    pending = enqueue(store, value_text="2", received_at_ms=100)
    result = store.prepare_next_attempt(
        device_id="123", session_id="session-a", prepared_at_ms=200,
        render=deterministic_renderer,
    )
    assert result.disposition is ClaimDisposition.PREPARED
    assert result.command.command_id == pending.command_id
    assert result.command.state is CommandState.AWAITING_ACK
    assert result.command.attempt_count == 1
    assert result.command.ack_deadline_ms == 200 + store.policy.ack_timeout_ms
    assert result.attempt is not None
    assert result.command.last_wire_frame == result.attempt.wire_frame
    assert result.attempt.write_outcome is AttemptWriteOutcome.PREPARED


def test_retry_preserves_stable_fields_and_refreshes_attempt_fields_only(
    store: TwinCommandStore, deterministic_renderer: RecordingRenderer
) -> None:
    first = prepare(store, deterministic_renderer, session="a", now_ms=200)
    store.mark_write_started(
        command_id=first.command.command_id, attempt_number=1,
        session_id="a", started_at_ms=201,
    )
    store.mark_write_unknown(
        command_id=first.command.command_id, attempt_number=1,
        session_id="a", occurred_at_ms=202, error="drain reset",
    )
    second = store.prepare_next_attempt(
        device_id="123", session_id="b", prepared_at_ms=300,
        render=deterministic_renderer,
    )
    assert (
        first.command.wire_id, first.command.wire_id_set, first.command.wire_dt,
        first.command.device_id, first.command.table_name, first.command.item_name,
        first.command.value_text,
    ) == (
        second.command.wire_id, second.command.wire_id_set, second.command.wire_dt,
        second.command.device_id, second.command.table_name, second.command.item_name,
        second.command.value_text,
    )
    assert first.attempt.tsec_text != second.attempt.tsec_text
    assert first.attempt.ver_text != second.attempt.ver_text
    assert first.attempt.crc_text != second.attempt.crc_text
```

Add exact CAS rejection for wrong command/attempt/session, `prepared -> started -> drained`, synchronous non-invocation failure as `failed`, post-invocation/drain uncertainty as `unknown`, stable audit IDs on every row, and no terminal confirmation from a drained write.

- [ ] **Step 3: Write ACK, NACK, event, timeout, and recovery tests**

Cover inclusive evidence deadlines and terminal immutability:

```python
def test_acknowledge_moves_to_awaiting_event_with_persisted_deadline(
    store: TwinCommandStore, deterministic_renderer: AttemptRenderer
) -> None:
    attempt = prepared_and_drained(store, deterministic_renderer, session="a")
    result = store.acknowledge_and_prepare_next(
        command_id=attempt.command_id, attempt_number=attempt.attempt_number,
        session_id="a", received_at_ms=attempt.ack_deadline_ms,
        response=SettingResponse(
            result="ACK", reason="Setting",
            rdt_text="06.08.2026 10:12:00", fingerprint="f" * 64,
        ),
        evidence_frame=b"ack",
        render=deterministic_renderer,
    )
    assert result.accepted_command.state is CommandState.AWAITING_EVENT
    assert result.accepted_command.event_deadline_ms == (
        attempt.ack_deadline_ms + store.policy.event_timeout_ms
    )


def test_attempt_limits_one_and_eight_and_nack_is_terminal(
    store_factory: Callable[[int], TwinCommandStore],
    deterministic_renderer: AttemptRenderer,
) -> None:
    one = store_factory(1)
    command = prepare(one, deterministic_renderer, session="a", now_ms=10)
    failed = one.release_for_retry(
        command_id=command.command.command_id, attempt_number=1,
        session_id="a", occurred_at_ms=11, reason=RetryReason.ACK_TIMEOUT,
    )
    assert failed.command.state is CommandState.FAILED
    eight = store_factory(8)
    exhaust_seven_uncertain_attempts(eight, deterministic_renderer)
    eighth = prepare(eight, deterministic_renderer, session="h", now_ms=80)
    assert eighth.command.attempt_count == 8
    assert eight.release_for_retry(
        command_id=eighth.command.command_id, attempt_number=8,
        session_id="h", occurred_at_ms=81, reason=RetryReason.DISCONNECT,
    ).command.state is CommandState.FAILED
    assert eight.prepare_next_attempt(
        device_id="123", session_id="i", prepared_at_ms=90,
        render=deterministic_renderer,
    ).disposition is ClaimDisposition.NO_ELIGIBLE


def test_duplicate_event_is_idempotent_after_reopen(
    store_path: Path, control_policy: ControlPolicy, exact_event: SettingEvent
) -> None:
    first = open_store_with_awaiting_event(store_path, control_policy)
    matched = first.record_event(evidence=exact_event, received_at_ms=500, evidence_frame=b"event")
    first.close()
    second = TwinCommandStore(store_path, policy=control_policy)
    second.open(now_ms=600)
    duplicate = second.record_event(
        evidence=exact_event, received_at_ms=600, evidence_frame=b"event"
    )
    assert matched.disposition is EventDisposition.CONFIRMED
    assert duplicate.disposition is EventDisposition.DUPLICATE
    assert duplicate.confirmation is None


def test_matcher_requires_exact_device_table_key_and_canonical_value(
    store_with_awaiting_event: TwinCommandStore,
    exact_event: SettingEvent,
) -> None:
    for changed in (
        event_with(exact_event, device_id="foreign"),
        event_with(exact_event, table_name="tbl_boiler_prms"),
        event_with(exact_event, item_name="PRRTY"),
        event_with(exact_event, new_value_text="3"),
    ):
        result = store_with_awaiting_event.record_event(
            evidence=changed,
            received_at_ms=500,
            evidence_frame=changed.content_text.encode("utf-8"),
        )
        assert result.disposition is EventDisposition.UNMATCHED
        assert result.confirmation is None
```

Place `test_matcher_requires_exact_device_table_key_and_canonical_value` in `tests/v2/test_setting_confirmation.py`; its `event_with()` helper must recompute the evidence ID from the changed envelope/content. Prove that event text `"2.0"` canonicalizes to and confirms a command value `"2"`, while a non-finite, off-step, out-of-range, or unknown-target event remains unmatched. Add tests for wrong session, late response, duplicate fingerprint across the same session batch, decreasing parseable Rdt, terminal NACK with diagnostic reason `WC`, exact device/table/key/canonical value event matching, wrong value/time, unmatched evidence never matching a future command, two distinct evidence IDs confirming two FIFO commands, direct event confirmation from `awaiting_ack`, ACK/event timeout transitions, pending TTL, all recovered states, and terminal-state preservation.

- [ ] **Step 4: Run lifecycle tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_twin_store.py tests/v2/test_twin_state.py tests/v2/test_setting_confirmation.py -q
```

Expected: FAIL because store mutation/result methods are not implemented.

- [ ] **Step 5: Add exact result types and store API**

Extend `twin/state.py` with frozen, slotted `EnqueueResult`, `ClaimResult`, `AckResult`, `NackResult`, `EventMatchResult`, `EventTimeoutCandidate`, `SweepReport`, and `TransitionAuditSnapshot`. Use these key fields:

```python
@dataclass(frozen=True, slots=True)
class ConfirmedSetting:
    command_id: str
    audit_id: str
    evidence_id: str
    device_id: str
    table_name: str
    item_name: str
    value_text: str
    confirmed_at_ms: int


@dataclass(frozen=True, slots=True)
class ClaimResult:
    disposition: ClaimDisposition
    command: TwinCommand | None
    attempt: CommandAttempt | None
    snapshots: tuple[TransitionAuditSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class AckResult:
    accepted_command: TwinCommand | None
    duplicate: bool
    next_claim: ClaimResult
    snapshots: tuple[TransitionAuditSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class NackResult:
    accepted_command: TwinCommand | None
    duplicate: bool
    snapshots: tuple[TransitionAuditSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class EventMatchResult:
    disposition: EventDisposition
    command: TwinCommand | None
    prior_state: CommandState | None
    active_session_id: str | None
    evidence: SettingEventReceipt
    confirmation: ConfirmedSetting | None
    snapshot: TransitionAuditSnapshot | None
```

Also define:

```python
class EventDisposition(str, Enum):
    CONFIRMED = "confirmed"
    UNMATCHED = "unmatched"
    DUPLICATE = "duplicate"


class LocalResponseDisposition(str, Enum):
    ACK_ACCEPTED = "ack_accepted"
    NEXT_SENT = "next_sent"
    NACK_ACCEPTED = "nack_accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
```

Also define:

```python
@dataclass(frozen=True, slots=True)
class EnqueueResult:
    command: TwinCommand
    superseded_command: TwinCommand | None
    snapshots: tuple[TransitionAuditSnapshot, ...]


@dataclass(frozen=True, slots=True)
class TransitionAuditSnapshot:
    command: TwinCommand
    transition: CommandTransition
    attempt: CommandAttempt | None = None
    evidence: SettingEventReceipt | None = None


@dataclass(frozen=True, slots=True)
class EventTimeoutCandidate:
    command_id: str
    device_id: str
    event_deadline_ms: int


@dataclass(frozen=True, slots=True)
class SweepReport:
    expired_pending: int
    retry_pending: int
    failed_attempt_limit: int
    incomplete_event_timeout: int
    snapshots: tuple[TransitionAuditSnapshot, ...]
```

`StoreStatus` contains exact counts for each `CommandState`, `nonterminal_commands`, `control_available`, and optional bounded degradation reason. `RecoveryReport` contains `expired_pending`, `retry_pending`, `failed_attempt_limit`, `kept_awaiting_event`, and `incomplete_event_timeout`. `mark_write_started`, `mark_attempt_drained`, `mark_write_failed`, `mark_write_unknown`, and `release_for_retry` return `TransitionAuditSnapshot`; callers read the updated row through `.command` and the attempt through `.attempt`. `mark_nack` returns `NackResult` so exact duplicates can remain no-op results without inventing a transition.

Define `RetryReason` values `write_failed`, `write_unknown`, `disconnect`, `ack_timeout`, `unexpected_response`, `stream_error`, and `shutdown`. Implement this exact synchronous public API; every argument after `self` is keyword-only except the immutable ingress envelope:

```python
class TwinCommandStore:
    def open(self, *, now_ms: int) -> None: ...
    def close(self) -> None: ...
    def verify_health(self) -> PragmaSnapshot: ...
    def pragma_snapshot(self) -> PragmaSnapshot: ...
    def observe_device(
        self, *, device_id: str, observed_at_ms: int,
        observed_wire_id: int, observed_wire_id_set: int,
    ) -> DeviceState: ...

    def record_ingress_disposition(
        self, ingress: ControlIngress, *, disposition: IngressDisposition,
        reason: str,
    ) -> ControlIngress: ...
    def record_proxy_control_ingress(
        self, ingress: ControlIngress, *, reason: str,
    ) -> ControlIngress: ...
    def enqueue_command(
        self, ingress: ControlIngress, *, device_id: str, table_name: str,
        item_name: str, value_text: str,
    ) -> EnqueueResult: ...
    def prepare_next_attempt(
        self, *, device_id: str, session_id: str, prepared_at_ms: int,
        render: AttemptRenderer,
    ) -> ClaimResult: ...

    def mark_write_started(
        self, *, command_id: str, attempt_number: int, session_id: str,
        started_at_ms: int,
    ) -> TransitionAuditSnapshot: ...
    def mark_attempt_drained(
        self, *, command_id: str, attempt_number: int, session_id: str,
        drained_at_ms: int,
    ) -> TransitionAuditSnapshot: ...
    def mark_write_failed(
        self, *, command_id: str, attempt_number: int, session_id: str,
        occurred_at_ms: int, error: str,
    ) -> TransitionAuditSnapshot: ...
    def mark_write_unknown(
        self, *, command_id: str, attempt_number: int, session_id: str,
        occurred_at_ms: int, error: str,
    ) -> TransitionAuditSnapshot: ...
    def acknowledge_and_prepare_next(
        self, *, command_id: str, attempt_number: int, session_id: str,
        response: SettingResponse, received_at_ms: int,
        evidence_frame: bytes, render: AttemptRenderer,
    ) -> AckResult: ...
    def mark_nack(
        self, *, command_id: str, attempt_number: int, session_id: str,
        response: SettingResponse, received_at_ms: int,
        evidence_frame: bytes,
    ) -> NackResult: ...
    def release_for_retry(
        self, *, command_id: str, attempt_number: int, session_id: str,
        occurred_at_ms: int, reason: RetryReason, error: str | None = None,
    ) -> TransitionAuditSnapshot: ...

    def record_event(
        self, *, evidence: SettingEvent, received_at_ms: int,
        evidence_frame: bytes,
    ) -> EventMatchResult: ...
    def sweep_deadlines(
        self, *, now_ms: int, include_event_timeouts: bool = True,
    ) -> SweepReport: ...
    def read_event_timeout_candidates(
        self, *, now_ms: int,
    ) -> tuple[EventTimeoutCandidate, ...]: ...
    def mark_event_incomplete(
        self, *, command_id: str, expected_event_deadline_ms: int,
        now_ms: int,
    ) -> TransitionAuditSnapshot | None: ...
    def recover(self, *, now_ms: int) -> RecoveryReport: ...
    def status_snapshot(self, device_id: str | None = None) -> StoreStatus: ...
    def read_device(self, device_id: str) -> DeviceState: ...
    def read_command(self, command_id: str) -> TwinCommand: ...
    def read_attempt(
        self, command_id: str, attempt_number: int,
    ) -> CommandAttempt: ...
    def read_ingress(self, ingress_id: str) -> ControlIngress: ...
    def read_latest_ingress(self) -> ControlIngress: ...
    def read_event_receipt(self, evidence_id: str) -> SettingEventReceipt: ...
    def read_transitions(
        self, command_id: str | None = None,
    ) -> tuple[CommandTransition, ...]: ...
    def single_nonterminal(self, device_id: str | None = None) -> TwinCommand: ...
```

`ControlIngress` carries the six required envelope fields `ingress_id`, `received_at_ms`, `topic`, `topic_device_id`, `retain`, and `raw_text`, followed by nullable/defaulted persisted fields `disposition`, `reason`, `command_id`, and `audit_id`. Read methods return immutable snapshots and raise `StoreRecordNotFound` for absence; mutation CAS mismatch raises `StaleAttemptError` and never returns a misleading success snapshot.

- [ ] **Step 6: Implement enqueue and claim transactions**

For every mutation, use this transaction shell and add a transition before commit:

```python
with self._mutex:
    self._require_healthy()
    self._conn.execute("BEGIN IMMEDIATE")
    try:
        result = operation(self._conn)
        self._conn.execute("COMMIT")
        return result
    except BaseException:
        self._conn.execute("ROLLBACK")
        raise
```

`enqueue_command()` must mark only an existing matching `pending/attempt_count=0` row `superseded`, preserve it, select the latest non-superseded same-target predecessor, insert one command plus `accepted_command` ingress link plus transition atomically, and set `pending_expires_at_ms = ingress.received_at_ms + policy.pending_ttl_ms`. Return committed snapshots in transition-ID order: optional supersession first, then the new enqueue.

`prepare_next_attempt()` must:

1. Reject if another `awaiting_ack` row owns the device; return `ACTIVE_DELIVERY_ELSEWHERE` when its session differs.
2. Select only the oldest eligible `pending` or `retry_pending` command for the exact device.
3. Block a successor while its predecessor is `pending`, `retry_pending`, or `awaiting_ack`.
4. Block an identical same-target/value successor while an earlier command is `awaiting_event`.
5. On first attempt, read the observed next counters, derive Czech civil `wire_dt` from `prepared_at_ms` using `ZoneInfo("Europe/Prague")`, and call the renderer before consuming counters.
6. On retry, reuse persisted `wire_id`, `wire_id_set`, and `wire_dt`.
7. Require five-digit `ver_text`, a version not used by a prior attempt of the command, five-digit CRC text, and exact `wire_frame` length.
8. Insert a `selected` self-edge, then the attempt, increment `attempt_count`, persist exact bytes, set `awaiting_ack`, UUID session, and inclusive ACK deadline, and insert the `attempt_prepared` state edge in the same transaction. Return both snapshots in transition-ID order.
9. If rendering raises or violates the contract, mark the command `failed` with `render_failed`; do not consume device counters and do not create an attempt.

- [ ] **Step 7: Implement write, response, evidence, and recovery transactions**

All write/response methods compare `command_id + attempt_number + session_id` and reject stale CAS input without touching a successor. `mark_write_started()` persists immediately before writer invocation. `mark_attempt_drained()` records drain time but leaves the command in `awaiting_ack`. `mark_write_failed()` and `mark_write_unknown()` transition to `retry_pending` below the configured limit and `failed` at the limit.

`acknowledge_and_prepare_next()` must accept only `received_at_ms <= ack_deadline_ms`, deduplicate SHA-256 response fingerprints across all attempts in the UUID session, reject decreasing parseable Rdt, persist response evidence, move to `awaiting_event`, clear active ownership, set `event_deadline_ms = received_at_ms + event_timeout_ms`, then invoke the internal in-transaction claim routine for the next eligible command. Its snapshots contain the ACK transition followed by every next-claim transition in transition-ID order. `mark_nack()` applies the same ownership/deadline/fingerprint rules, moves directly to `failed`, stores the bounded reason, never claims a successor, and returns an empty snapshot tuple for an exact duplicate.

`record_event()` must insert the receipt first; on duplicate `evidence_id`, increment `duplicate_count/last_seen_at_ms` and never rematch. For a new receipt, pass `new_value_text` through Task 4's exact target constraint and compare its canonical result, without changing the raw receipt or evidence ID. Invalid/non-allowlisted event values remain unmatched. Match the oldest exact `awaiting_event` command with `acked_at_ms <= received_at_ms <= event_deadline_ms`; if none, match the exact active `awaiting_ack` command with `prepared_at_ms <= received_at_ms <= ack_deadline_ms`. Compare parseable event DT against stored ACK Rdt as supporting order. Link at most one command, commit `confirmed`, and return `ConfirmedSetting`; otherwise persist `unmatched` forever.

`sweep_deadlines()` uses strict `now_ms > deadline_ms`, leaving exact-deadline evidence admissible. It expires never-attempted pending commands and retries/fails `awaiting_ack`; it marks `awaiting_event` incomplete only in unit/startup reconciliation when `include_event_timeouts=True`. `read_event_timeout_candidates()` is read-only and returns overdue rows ordered by `(event_deadline_ms, command_id)`. `mark_event_incomplete()` performs an exact CAS on `command_id + awaiting_event + expected_event_deadline_ms + now_ms > deadline`, returns null if state/deadline changed, and is the only runtime event-timeout mutation used by Task 8. `recover()` performs the approved startup mapping, preserves stable fields and terminal rows, and returns exact counts; recovery may reconcile overdue events immediately because no socket-scoped received token survives restart.

- [ ] **Step 8: Run lifecycle tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_twin_store.py tests/v2/test_twin_state.py tests/v2/test_setting_confirmation.py -q
```

Expected: PASS for atomic ingress, immutable successors, exact-device FIFO, stable retry identity, attempt limits, terminal NACK, durable deduplication, deadlines, and restart recovery.

- [ ] **Step 9: Commit the complete store lifecycle**

```bash
git add addon/oig-proxy/twin/state.py addon/oig-proxy/twin/store.py tests/v2/conftest.py tests/v2/test_twin_state.py tests/v2/test_twin_store.py tests/v2/test_setting_confirmation.py
git commit -m "feat: persist local-setting transaction lifecycle"
```

---

### Task 8: Coordinate Per-Device Delivery and Project Committed Audit

**Files:**
- Modify: `addon/oig-proxy/twin/delivery.py`
- Modify: `addon/oig-proxy/twin/state.py`
- Modify: `addon/oig-proxy/twin/store.py`
- Rewrite: `addon/oig-proxy/telemetry/settings_audit.py`
- Modify: `addon/oig-proxy/telemetry/collector.py`
- Rewrite: `tests/v2/test_twin_delivery.py`
- Rewrite: `tests/v2/test_settings_audit_contract.py`
- Modify: `tests/v2/test_telemetry_collector_extra.py`
- Modify: `tests/v2/conftest.py`

**Interfaces:**
- Consumes: Complete Task 7 store, Task 4 renderer, Task 5 typed evidence, and `ControlPolicy`.
- Produces: `TwinCoordinator`, `LocalSettingWriter` protocol, delivery/response/event decisions, deadline sweeper, `SettingsAuditPublisher`, and `ConfirmedSetting` for Tasks 9 through 11.

- [ ] **Step 1: Write coordinator authorization and writer-order tests**

Use a scripted writer that records the store state visible at invocation:

```python
@pytest.mark.asyncio
async def test_claim_requires_correlated_cloud_terminal_end(
    coordinator: TwinCoordinator, scripted_writer: ScriptedLocalSettingWriter
) -> None:
    rejected = await coordinator.claim_and_write_next(
        device_id="123", session_id="session-a", received_at_ms=200,
        trigger=None, writer=scripted_writer,  # type: ignore[arg-type]
    )
    assert rejected.disposition is DeliveryDisposition.UNAUTHORIZED
    assert scripted_writer.frames == []
    assert (await coordinator.status_snapshot("123")).awaiting_ack == 0


@pytest.mark.asyncio
async def test_deliver_next_writes_only_durably_prepared_frame(
    coordinator: TwinCoordinator, scripted_writer: ScriptedLocalSettingWriter
) -> None:
    decision = await coordinator.claim_and_write_next(
        device_id="123", session_id="session-a", received_at_ms=200,
        trigger=DeliveryTrigger.CORRELATED_CLOUD_END,
        writer=scripted_writer,
    )
    assert decision.disposition is DeliveryDisposition.SENT
    assert scripted_writer.frames == [decision.active_attempt.wire_frame]
    assert scripted_writer.state_at_invocation is CommandState.AWAITING_ACK
    assert decision.active_attempt.write_outcome is AttemptWriteOutcome.DRAINED


@pytest.mark.asyncio
async def test_disabled_coordinator_never_claims_or_writes(
    disabled_coordinator: TwinCoordinator,
    scripted_writer: ScriptedLocalSettingWriter,
) -> None:
    before = await disabled_coordinator.status_snapshot("123")
    result = await disabled_coordinator.claim_and_write_next(
        device_id="123", session_id="session-a", received_at_ms=200,
        trigger=DeliveryTrigger.OFFLINE_ISNEWSET, writer=scripted_writer,
    )
    assert result.disposition is DeliveryDisposition.CONTROL_DISABLED
    assert await disabled_coordinator.status_snapshot("123") == before
    assert scripted_writer.frames == []


@pytest.mark.asyncio
async def test_rapid_same_key_updates_preserve_attempted_predecessor(
    coordinator: TwinCoordinator,
    scripted_writer: ScriptedLocalSettingWriter,
) -> None:
    first = await coordinator.claim_and_write_next(
        device_id="123", session_id="a", received_at_ms=200,
        trigger=DeliveryTrigger.OFFLINE_ISNEWSET, writer=scripted_writer,
    )
    second = enqueue(coordinator.store, value_text="3", received_at_ms=210)
    assert (await coordinator.read_command(first.active_attempt.command_id)).value_text == "2"
    assert second.predecessor_command_id == first.active_attempt.command_id


@pytest.mark.asyncio
async def test_disconnect_requeues_same_wire_identity_for_next_dialogue(
    coordinator: TwinCoordinator,
    scripted_writer: ScriptedLocalSettingWriter,
) -> None:
    first = await deliver(coordinator, scripted_writer, session="a", now_ms=200)
    await coordinator.abort_dialogue(
        active=first.active_attempt, occurred_at_ms=210,
        reason=RetryReason.DISCONNECT,
    )
    second = await deliver(coordinator, scripted_writer, session="b", now_ms=300)
    assert stable_wire_fields(second.active_attempt.wire_frame) == stable_wire_fields(
        first.active_attempt.wire_frame
    )


@pytest.mark.asyncio
async def test_timeout_stops_at_limit_and_nack_never_retries(
    coordinator_at_limit: TwinCoordinator,
    scripted_writer: ScriptedLocalSettingWriter,
    valid_setting_nack: SettingResponse,
) -> None:
    active = await deliver_at_limit(coordinator_at_limit, scripted_writer)
    timed_out = await coordinator_at_limit.abort_dialogue(
        active=active, occurred_at_ms=active.ack_deadline_ms + 1,
        reason=RetryReason.ACK_TIMEOUT,
    )
    assert timed_out.command.state is CommandState.FAILED
    nack_active = await deliver_fresh(coordinator_at_limit, scripted_writer)
    nack = await coordinator_at_limit.handle_local_response(
        active=nack_active, response=valid_setting_nack,
        context=evidence_context_for(nack_active), writer=scripted_writer,
    )
    assert nack.disposition is LocalResponseDisposition.NACK_ACCEPTED
    assert nack.next_attempt is None
```

Add scripted `write()`-before-invocation failure, `drain()` uncertainty, successful drain, active owner elsewhere, distinct-device parallel writes, and exact attempt-limit outcomes.

- [ ] **Step 2: Write local response, direct-event, and deadline-race tests**

Use `EvidenceContext` with explicit direction/session/device/timestamp:

```python
@pytest.mark.asyncio
async def test_ack_moves_to_awaiting_event_without_state_publication(
    coordinator: TwinCoordinator,
    active_attempt: ActiveLocalAttempt,
    valid_setting_ack: SettingResponse,
    scripted_writer: ScriptedLocalSettingWriter,
) -> None:
    decision = await coordinator.handle_local_response(
        active=active_attempt,
        response=valid_setting_ack,
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY, "session-a", "123",
            active_attempt.ack_deadline_ms, valid_setting_ack_raw,
        ),
        writer=scripted_writer,
    )
    assert decision.disposition in {
        LocalResponseDisposition.ACK_ACCEPTED,
        LocalResponseDisposition.NEXT_SENT,
    }
    assert (await coordinator.read_command(active_attempt.command_id)).state is CommandState.AWAITING_EVENT
    assert decision.confirmation is None


@pytest.mark.asyncio
async def test_ack_requires_active_session_and_dialog_owner(
    coordinator: TwinCoordinator,
    active_attempt: ActiveLocalAttempt,
    valid_setting_ack: SettingResponse,
    scripted_writer: ScriptedLocalSettingWriter,
) -> None:
    result = await coordinator.handle_local_response(
        active=active_attempt,
        response=valid_setting_ack,
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY, "foreign-session", "123", 201,
            b"foreign",
        ),
        writer=scripted_writer,
    )
    assert result.disposition is LocalResponseDisposition.REJECTED
    assert result.close_connection is True
    assert (await coordinator.read_command(active_attempt.command_id)).state in {
        CommandState.RETRY_PENDING, CommandState.FAILED,
    }


@pytest.mark.asyncio
async def test_in_deadline_event_wins_when_sweeper_waits_for_device_lock(
    coordinator: TwinCoordinator, exact_event: SettingEvent
) -> None:
    first_sweep = await coordinator.sweep_deadlines(now_ms=501)
    assert first_sweep.incomplete_event_timeout == 0
    token = coordinator.register_setting_event(
        event=exact_event,
        context=EvidenceContext(
            FrameDirection.BOX_TO_PROXY, "session-a", "123", 500, b"event"
        ),
    )
    decision = await coordinator.handle_registered_event(token)
    await coordinator.sweep_deadlines(now_ms=1502)
    assert decision.disposition is EventDisposition.CONFIRMED
    assert decision.confirmation is not None
    assert (
        await coordinator.read_command(decision.confirmation.command_id)
    ).state is CommandState.CONFIRMED
```

Add exact duplicate response fingerprint, decreasing Rdt, wrong direction/device, stale ACK, NACK terminal behavior, same-value successor blocking, event matcher precedence (`awaiting_event` before direct `awaiting_ack`), direct event closing its owning dialogue, unmatched/duplicate event no callback, and sweeper-first/event-first interleavings.

- [ ] **Step 3: Write committed audit identity tests**

Replace in-memory terminal-precedence expectations with persisted snapshot projection:

```python
def test_write_outcomes_and_telemetry_reuse_persisted_identity(
    snapshots: tuple[TransitionAuditSnapshot, ...]
) -> None:
    records: list[SettingsAuditRecord] = []
    publisher = SettingsAuditPublisher(records.append)
    for snapshot in snapshots:
        publisher.publish_committed(snapshot)
    assert {record.command_id for record in records} == {snapshots[0].command.command_id}
    assert {record.audit_id for record in records} == {snapshots[0].command.audit_id}
    assert {record.msg_id for record in records if record.msg_id is not None} == {
        snapshots[0].command.wire_id
    }
    assert {record.id_set for record in records if record.id_set is not None} == {
        snapshots[0].command.wire_id_set
    }
    assert {record.write_outcome for record in records} >= {
        "prepared", "started", "drained"
    }


def test_audit_sink_failure_does_not_change_committed_state(
    committed_snapshot: TransitionAuditSnapshot,
) -> None:
    def failing_sink(_: SettingsAuditRecord) -> None:
        raise RuntimeError("telemetry unavailable")
    SettingsAuditPublisher(failing_sink).publish_committed(committed_snapshot)
    assert committed_snapshot.command.state is CommandState.AWAITING_ACK


def test_cloud_setting_observer_is_session_local_and_never_mutates_store(
    store: TwinCommandStore,
    cloud_setting_frame: ValidatedFrame,
    cloud_setting_metadata: FrameMetadata,
) -> None:
    before = store.status_snapshot()
    records: list[CloudSettingAuditRecord] = []
    observer = CloudSettingAuditObserver(records.append)
    observation = observer.setting_forwarded(
        session_id="session-a",
        frame=cloud_setting_frame,
        metadata=cloud_setting_metadata,
        observed_at_ms=100,
    )
    observer.box_response_forwarded(
        session_id="session-a", response=setting_ack(), observed_at_ms=110
    )
    assert observation.wire_id == cloud_setting_metadata.message_id
    assert observation.wire_id_set == cloud_setting_metadata.id_set
    assert store.status_snapshot() == before
```

Add projection cases for enqueued, superseded, selected, prepared, started, drained, unknown, failed, ACK, retry, NACK, event-confirmed, expired, and incomplete transitions; exact wire/evidence bytes; redaction; collector JSON serialization; and no generated replacement identity. Add passive cloud cases for multiple Settings in one UUID session, forwarded BOX ACK sequence, exact event observation, session close/timeout, and proof that cloud direction never creates or mutates a local command.

- [ ] **Step 4: Run coordinator and audit tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_twin_delivery.py tests/v2/test_settings_audit_contract.py tests/v2/test_telemetry_collector_extra.py -q
```

Expected: FAIL because the coordinator decisions, writer protocol, event registration, and committed projection do not exist.

- [ ] **Step 5: Define coordinator-facing state contracts**

Add these frozen types in `twin/state.py`:

```python
class DeliveryTrigger(str, Enum):
    CORRELATED_CLOUD_END = "correlated_cloud_end"
    OFFLINE_ISNEWSET = "offline_isnewset"
    LOCAL_ACK_CONTINUATION = "local_ack_continuation"


class DeliveryDisposition(str, Enum):
    SENT = "sent"
    NO_ELIGIBLE = "no_eligible"
    ACTIVE_DELIVERY_ELSEWHERE = "active_delivery_elsewhere"
    CONTROL_DISABLED = "control_disabled"
    UNAUTHORIZED = "unauthorized"
    RENDER_FAILED = "render_failed"
    WRITE_FAILED = "write_failed"
    WRITE_UNKNOWN = "write_unknown"


@dataclass(frozen=True, slots=True)
class ActiveLocalAttempt:
    command_id: str
    audit_id: str
    device_id: str
    attempt_number: int
    session_id: str
    ack_deadline_ms: int
    wire_frame: bytes
    write_outcome: AttemptWriteOutcome


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    direction: FrameDirection
    session_id: str
    device_id: str
    received_at_ms: int
    raw_frame: bytes


```

Define the coordinator decisions exactly:

```python
@dataclass(frozen=True, slots=True)
class AttemptWriteResult:
    outcome: AttemptWriteOutcome
    started_at_ms: int
    drain_completed_at_ms: int | None
    error_text: str | None


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    disposition: DeliveryDisposition
    active_attempt: ActiveLocalAttempt | None
    close_connection: bool
    snapshots: tuple[TransitionAuditSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalResponseDecision:
    disposition: LocalResponseDisposition
    command: TwinCommand | None
    next_attempt: ActiveLocalAttempt | None
    send_final_end: bool
    close_connection: bool
    snapshots: tuple[TransitionAuditSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class RegisteredEventToken:
    token_id: str
    event: SettingEvent
    context: EvidenceContext


class LocalSettingWriter(Protocol):
    async def write_attempt(
        self,
        attempt: ActiveLocalAttempt,
        *,
        before_write: Callable[[], Awaitable[None]],
    ) -> AttemptWriteResult: ...
```

Implement these exact coordinator signatures:

```python
class TwinCoordinator:
    async def claim_and_write_next(
        self, *, device_id: str, session_id: str, received_at_ms: int,
        trigger: DeliveryTrigger | None, writer: LocalSettingWriter,
    ) -> DeliveryDecision: ...

    async def handle_local_response(
        self, *, active: ActiveLocalAttempt, response: SettingResponse,
        context: EvidenceContext, writer: LocalSettingWriter,
    ) -> LocalResponseDecision: ...

    def register_setting_event(
        self, *, event: SettingEvent, context: EvidenceContext,
    ) -> RegisteredEventToken: ...

    async def handle_registered_event(
        self, token: RegisteredEventToken,
    ) -> EventMatchResult: ...

    async def flush_registered_events(
        self, *, session_id: str,
    ) -> tuple[EventMatchResult, ...]: ...

    async def abort_dialogue(
        self, *, active: ActiveLocalAttempt, occurred_at_ms: int,
        reason: RetryReason,
    ) -> TransitionAuditSnapshot: ...

    async def sweep_deadlines(self, *, now_ms: int | None = None) -> SweepReport: ...
    async def status_snapshot(self, device_id: str | None = None) -> StoreStatus: ...
    async def read_command(self, command_id: str) -> TwinCommand: ...
```

Keep a nonblocking `cached_status_snapshot` updated after each committed mutation for synchronous telemetry callbacks; it is observability only and never drives protocol decisions.

The production renderer samples `secrets.randbelow(65536)` at most 16 times until it obtains a value absent from `AttemptRenderContext.used_ver_texts`; if collisions exhaust those samples, select the first unused integer in `0..65535`. Format it as five digits, generate UTC `TSec` no earlier than the stable first-attempt instant, call Task 4's serializer, and return exact bytes/CRC. If all values were somehow exhausted, raise a render failure before consuming counters.

- [ ] **Step 6: Implement `TwinCoordinator` and deadline registration**

Add `TwinCoordinator` beside the temporarily importable legacy `TwinDelivery`; only unchanged pre-cutover startup paths may still construct the legacy class, and Task 13 deletes it. Create one `asyncio.Lock` per exact device and route every blocking store call through `asyncio.to_thread()`. Use this sequencing for a delivery:

```python
async with self._device_lock(device_id):
    claim = await asyncio.to_thread(
        self._store.prepare_next_attempt,
        device_id=device_id,
        session_id=session_id,
        prepared_at_ms=received_at_ms,
        render=self._renderer,
    )
    if claim.disposition is not ClaimDisposition.PREPARED:
        return self._delivery_from_claim(claim)
    active = active_attempt_from(claim)

    async def before_write() -> None:
        snapshot = await asyncio.to_thread(
            self._store.mark_write_started,
            command_id=active.command_id,
            attempt_number=active.attempt_number,
            session_id=active.session_id,
            started_at_ms=self._clock_ms(),
        )
        self._audit.publish_committed(snapshot)

    result = await writer.write_attempt(active, before_write=before_write)
    return await self._commit_write_result(active, result)
```

Reject any delivery trigger outside the three enum values before locking or store mutation. Hold the per-device lock through prepare, write-start commit, serialized writer call, and outcome commit. ACK uses the store's atomic ACK-plus-next-claim method and writes the already-prepared successor without a second claim. NACK never writes a successor. Rejected/unexpected active-dialogue evidence commits retry/failure before returning `close_connection=True`.

Make `register_setting_event()` a synchronous method that stores the exact already-captured receipt token before returning an awaitable token. `handle_registered_event()` acquires the device lock, commits the receipt, then removes the token. `flush_registered_events(session_id=...)` drains that session's tokens in receipt order through the same handler and returns their committed results; it is used during router cleanup so a token received before cancellation is never lost or left blocking the sweeper. The BOX read pump in Task 10 must reserve a CRC-valid parsed event synchronously immediately after complete-frame assembly and before its first subsequent `await`.

Use a two-pass in-process event-timeout candidate map so lock order cannot erase an already-received boundary event. Each pass first calls `sweep_deadlines(now_ms=..., include_event_timeouts=False)` for pending/ACK work, then reads exact event candidates. On the first observation of a candidate, record `(command_id, device_id, event_deadline_ms, first_overdue_monotonic)` but do not mark `incomplete`. On the next sweep at least `1.0` second later, acquire that candidate's device lock, skip the transition if a registered token has `received_at_ms <= event_deadline_ms`, otherwise call `mark_event_incomplete()` with the exact command/deadline CAS and publish only a returned snapshot. Never call the global event-timeout sweep from the runtime coordinator. Clear the candidate if state/deadline changed. `handle_registered_event()` always runs before timeout when its token was reserved by the pump, even when the sweeper acquired the device lock during the first overdue pass. Startup recovery has no socket buffer and may reconcile overdue events immediately. Run `verify_health()` and sweep passes every `1.0` second while nonterminal rows exist.

- [ ] **Step 7: Implement stateless committed audit projection**

Define lifecycle values exactly:

```python
class SettingStep(str, Enum):
    ENQUEUED = "enqueued"
    SUPERSEDED = "superseded"
    SELECTED = "selected"
    ATTEMPT_PREPARED = "attempt_prepared"
    WRITE_STARTED = "write_started"
    ATTEMPT_DRAINED = "attempt_drained"
    WRITE_UNKNOWN = "write_unknown"
    WRITE_FAILED = "write_failed"
    ACK_OBSERVED = "ack_observed"
    RETRY = "retry"
    NACK = "nack"
    EVENT_CONFIRMED = "event_confirmed"
    EXPIRED = "expired"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
```

Extend `SettingsAuditRecord` with transition ID, command ID, attempt number, states, wire DT, `TSec`, version, CRC, write outcome/length/frame, evidence ID/frame, and error. Populate `msg_id` and `id_set` only from persisted `wire_id` and `wire_id_set`. `SettingsAuditPublisher.publish_committed(snapshot)` catches/logs sink failure but never changes the snapshot or store. Keep `TelemetryCollector.record_setting_audit_step(record)` as a batching sink and preserve the existing bounded redaction policy.

Preserve cloud-originated audit through a separate connection-local observer:

```python
@dataclass(frozen=True, slots=True)
class CloudSettingAuditRecord:
    cloud_observation_id: str
    session_id: str
    device_id: str
    table_name: str
    item_name: str
    value_text: str
    wire_id: int | None
    wire_id_set: int | None
    raw_frame: bytes
    step: str
    observed_at_ms: int


class CloudSettingAuditObserver:
    def __init__(self, sink: Callable[[CloudSettingAuditRecord], None] | None) -> None:
        self._sink = sink
        self._sessions: dict[str, deque[CloudSettingAuditRecord]] = {}
```

Derive `cloud_observation_id` from SHA-256 of UUID session plus exact cloud Setting bytes; retain actual cloud wire IDs/raw bytes; correlate only by sequential connection-local order; clear the queue on session close. Its `setting_forwarded`, `box_response_forwarded`, `setting_event_observed`, and `close_session` methods publish passive telemetry only. It must not import `TwinCommandStore`, produce a local `command_id`, invoke confirmed-state publication, alter cloud payloads, or affect dialogue decisions.

- [ ] **Step 8: Run coordinator and audit tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_twin_delivery.py tests/v2/test_settings_audit_contract.py tests/v2/test_telemetry_collector_extra.py -q
```

Expected: PASS for authorization, durable-before-write ordering, session/direction correlation, delivery-only ACK, direct execution, races, passive status, and identity-preserving audit.

- [ ] **Step 9: Commit coordinator and audit projection**

```bash
git add addon/oig-proxy/twin/state.py addon/oig-proxy/twin/store.py addon/oig-proxy/twin/delivery.py addon/oig-proxy/telemetry/settings_audit.py addon/oig-proxy/telemetry/collector.py tests/v2/conftest.py tests/v2/test_twin_delivery.py tests/v2/test_settings_audit_contract.py tests/v2/test_telemetry_collector_extra.py
git commit -m "feat: coordinate local-setting delivery evidence"
```

---

### Task 9: Add Connection-Local Dialogue State and One BOX Writer

**Files:**
- Create: `addon/oig-proxy/proxy/dialog.py`
- Create: `addon/oig-proxy/proxy/writer.py`
- Modify: `addon/oig-proxy/capture/frame_capture.py`
- Create: `tests/v2/test_setting_dialog.py`
- Create: `tests/v2/test_proxy/test_writer.py`
- Modify: `tests/v2/test_capture_modules.py`
- Modify: `tests/v2/conftest.py`

**Interfaces:**
- Consumes: `AssembledFrame`, `FrameMetadata`, `DeliveryTrigger`, `ActiveLocalAttempt`, `AttemptWriteResult`, and existing capture sink.
- Produces: `SettingDialog`, bounded held queues, `SerializedBoxWriter`, `BoxWritePurpose`, and `AttemptCaptureLink` consumed by Task 10.

- [ ] **Step 1: Write pure dialogue-state tests**

Create `tests/v2/test_setting_dialog.py` with raw byte fixtures that are never normalized:

```python
def test_cloud_setting_marks_cycle_cloud_owned_without_local_claim() -> None:
    dialog = SettingDialog(session_id="s", route=SessionRoute.ONLINE)
    expectation = dialog.open_forwarded_request(
        kind=RequestKind.IS_NEW_SET,
        request_raw=b"poll\r\n",
        opened_at_monotonic=1.0,
        cloud_timeout_s=30.0,
    )
    dialog.mark_cloud_setting(b"cloud-setting\r\n")
    assert dialog.current_expectation() is expectation
    assert expectation.phase is CyclePhase.WAITING_BOX_CLOUD_ACK
    assert expectation.cloud_setting_count == 1
    assert dialog.active_attempt is None


def test_cloud_setting_ack_is_continuation_not_new_request() -> None:
    dialog = cloud_owned_dialog()
    size = dialog.expectation_count
    dialog.mark_cloud_setting_ack_forwarded(b"box-ack\r\n")
    assert dialog.expectation_count == size
    assert dialog.current_expectation().phase is CyclePhase.WAITING_CLOUD


def test_deferred_end_is_returned_byte_exact_after_final_ack() -> None:
    raw_end = b"<Frame><Result>END</Result><CRC>12345</CRC></Frame>\r\n"
    dialog = cloud_waiting_dialog()
    trigger = dialog.defer_correlated_terminal_end(raw_end)
    assert trigger is DeliveryTrigger.CORRELATED_CLOUD_END
    dialog.begin_local_attempt(active_attempt("command-1", 1))
    assert dialog.take_deferred_end_and_close_cycle() == raw_end
    assert dialog.current_expectation() is None


def test_local_delivery_trigger_accepts_only_isnewset() -> None:
    dialog = SettingDialog(session_id="s", route=SessionRoute.ONLINE)
    dialog.open_forwarded_request(
        kind=RequestKind.SINGLE_RESPONSE, request_raw=b"weather",
        opened_at_monotonic=1.0, cloud_timeout_s=None,
    )
    with pytest.raises(DialogStateError, match="not an IsNewSet cycle"):
        dialog.defer_correlated_terminal_end(b"end")
```

Add tests for tainted-cycle prohibition, FIFO head correlation, later BOX/cloud holds, exact 1 MiB held queue, one-byte overflow, identity change rejection, absolute cloud deadline across multiple Settings, and `clear_socket_state()` erasing deferred/held/session attempt data.

- [ ] **Step 2: Write serialized writer and capture-link tests**

Use a blocking dummy writer to force deterministic interleavings:

```python
@pytest.mark.asyncio
async def test_dialogue_owner_blocks_getactual_until_release() -> None:
    raw = BlockingStreamWriter()
    writer = SerializedBoxWriter(raw, clock_ms=iter((10, 11, 12, 13)).__next__)
    await writer.acquire_dialogue("session-a")
    getactual = asyncio.create_task(
        writer.write_frame(b"getactual", purpose=BoxWritePurpose.LOCAL_GETACTUAL)
    )
    await asyncio.sleep(0)
    assert raw.writes == []
    await writer.write_frame(
        b"setting", purpose=BoxWritePurpose.LOCAL_SETTING,
        owner_session_id="session-a",
    )
    await writer.release_dialogue("session-a")
    await getactual
    assert raw.writes == [b"setting", b"getactual"]


@pytest.mark.asyncio
async def test_writer_runs_before_write_inside_lock() -> None:
    observed: list[str] = []
    raw = RecordingStreamWriter(observed)
    writer = SerializedBoxWriter(raw, clock_ms=lambda: 10)

    async def before_write() -> None:
        observed.append("durable-start")

    result = await writer.write_attempt(
        active_attempt("command-1", 1), before_write=before_write
    )
    assert observed == ["durable-start", "write", "drain"]
    assert result.outcome is AttemptWriteOutcome.DRAINED


def test_frame_capture_persists_command_attempt_link(tmp_path: Path) -> None:
    capture = FrameCapture(db_path=str(tmp_path / "capture.db"))
    link = AttemptCaptureLink("command-1", "audit-1", 2)
    capture.capture(
        "123", "tbl_box_prms", "frame", b"frame", {}, "proxy_to_box",
        attempt_link=link,
    )
    row = capture.fetch_latest_for_attempt("command-1", 2)
    assert (row.command_id, row.audit_id, row.attempt_number) == (
        "command-1", "audit-1", 2
    )
```

Add concurrent raw-write serialization, same-owner Setting then END order, wrong-owner rejection, synchronous `write()` failure as `FAILED`, drain failure as `UNKNOWN`, observer exact bytes after invocation, capture invocation even when `write()` raises, capture failure isolation, and idempotent owner release.

- [ ] **Step 3: Run dialogue/writer tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_setting_dialog.py tests/v2/test_proxy/test_writer.py tests/v2/test_capture_modules.py -q
```

Expected: FAIL because dialogue, writer, held-queue, and capture-link types do not exist.

- [ ] **Step 4: Implement connection-local dialogue contracts**

Create exact enums and state records:

```python
class SessionRoute(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"


class RequestKind(str, Enum):
    IS_NEW_SET = "is_new_set"
    SINGLE_RESPONSE = "single_response"


class CyclePhase(str, Enum):
    WAITING_CLOUD = "waiting_cloud"
    WAITING_BOX_CLOUD_ACK = "waiting_box_cloud_ack"
    LOCAL_AWAITING_ACK = "local_awaiting_ack"
    TAINTED = "tainted"


@dataclass(slots=True)
class ResponseExpectation:
    sequence: int
    kind: RequestKind
    request_raw: bytes
    opened_at_monotonic: float
    deadline_monotonic: float | None
    phase: CyclePhase
    cloud_setting_count: int = 0


class DialogStateError(RuntimeError):
    pass


class HeldFrameOverflow(DialogStateError):
    pass


class BoundedFrameQueue:
    def __init__(self, *, max_bytes: int = 1_048_576) -> None: ...
    def append(self, frame: AssembledFrame) -> None: ...
    def drain(self) -> tuple[AssembledFrame, ...]: ...
    def clear(self) -> None: ...

    @property
    def byte_count(self) -> int: ...


@dataclass(slots=True)
class SettingDialog:
    session_id: str
    route: SessionRoute
    bound_device_id: str | None = None
    deferred_end: bytes | None = None
    active_attempt: ActiveLocalAttempt | None = None

    def bind_device(self, device_id: str) -> bool: ...
    def open_forwarded_request(
        self, *, kind: RequestKind, request_raw: bytes,
        opened_at_monotonic: float, cloud_timeout_s: float | None,
    ) -> ResponseExpectation: ...
    def current_expectation(self) -> ResponseExpectation | None: ...
    def mark_cloud_setting(self, raw: bytes) -> None: ...
    def mark_cloud_setting_ack_forwarded(self, raw: bytes) -> None: ...
    def defer_correlated_terminal_end(self, raw: bytes) -> DeliveryTrigger: ...
    def begin_local_attempt(self, attempt: ActiveLocalAttempt) -> None: ...
    def replace_local_attempt(self, attempt: ActiveLocalAttempt) -> None: ...
    def take_deferred_end_and_close_cycle(self) -> bytes: ...
    def taint_current_cycle(self) -> None: ...
    def hold_box_frame(self, frame: AssembledFrame) -> None: ...
    def hold_cloud_frame(self, frame: AssembledFrame) -> None: ...
    def drain_held_box(self) -> tuple[AssembledFrame, ...]: ...
    def drain_held_cloud(self) -> tuple[AssembledFrame, ...]: ...
    def clear_socket_state(self) -> None: ...
```

`BoundedFrameQueue.append(frame)` must reject any addition that makes `byte_count > 1_048_576`, while allowing equality. `SettingDialog` stores UUID `session_id`, frozen route, exact bound device, FIFO expectations, deferred raw END, one active local attempt, and separate held BOX/cloud queues. It exposes `bind_device`, `open_forwarded_request`, `mark_cloud_setting`, `mark_cloud_setting_ack_forwarded`, `defer_correlated_terminal_end`, `begin_local_attempt`, `replace_local_attempt`, `take_deferred_end_and_close_cycle`, `taint_current_cycle`, hold/drain methods, `is_cloud_deadline_expired`, and `clear_socket_state`. Only an untainted FIFO-head `IS_NEW_SET` expectation may return the correlated delivery trigger.

- [ ] **Step 5: Implement one semantic/serialized BOX writer**

Define:

```python
class BoxWritePurpose(str, Enum):
    CLOUD_FORWARD = "cloud_forward"
    LOCAL_SETTING = "local_setting"
    LOCAL_GETACTUAL = "local_getactual"
    OFFLINE_RESPONSE = "offline_response"
    DEFERRED_END = "deferred_end"


class BoxWriteOutcome(str, Enum):
    DRAINED = "drained"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BoxWriteResult:
    outcome: BoxWriteOutcome
    started_at_ms: int
    drain_completed_at_ms: int | None
    error_text: str | None


@dataclass(frozen=True, slots=True)
class AttemptCaptureLink:
    command_id: str
    audit_id: str
    attempt_number: int
```

Place `AttemptCaptureLink` in `capture/frame_capture.py` so capture owns its storage link and `proxy/writer.py` imports it without a capture-to-proxy cycle. Implement:

```python
class SerializedBoxWriter:
    def __init__(
        self,
        writer: asyncio.StreamWriter,
        *,
        clock_ms: Callable[[], int],
        on_invoked: Callable[
            [bytes, BoxWritePurpose, AttemptCaptureLink | None], None
        ] | None = None,
    ) -> None: ...

    async def acquire_dialogue(self, session_id: str) -> None: ...
    async def release_dialogue(self, session_id: str) -> None: ...
    async def write_frame(
        self, frame: bytes, *, purpose: BoxWritePurpose,
        owner_session_id: str | None = None,
    ) -> BoxWriteResult: ...
    async def write_attempt(
        self, attempt: ActiveLocalAttempt, *,
        before_write: Callable[[], Awaitable[None]],
    ) -> AttemptWriteResult: ...
```

`SerializedBoxWriter` owns one `asyncio.Lock` plus one `asyncio.Condition` and optional dialogue owner UUID. `write_frame()` waits while a different dialogue owns semantic output, invokes `writer.write()` inside the serialization lock, calls the observer with exact bytes/purpose/link immediately after invocation (including a caught synchronous exception), awaits `drain()`, and returns explicit `DRAINED`, `FAILED`, or `UNKNOWN`. `write_attempt()` implements Task 8's protocol, executes `before_write` immediately before the underlying invocation, and uses an `AttemptCaptureLink` derived from the active attempt. Never retry inside the writer.

Pause GetActual and unrelated cloud/offline output through the same condition. Only the owning UUID may write a local Setting or deferred END while ownership is active. Release wakes all waiters but does not reorder the writer lock's acquired sequence.

- [ ] **Step 6: Extend capture storage without making it lifecycle truth**

Add nullable `command_id`, `audit_id`, and `attempt_number` columns to the frame-capture table plus index `(command_id, attempt_number)`. Make its migration idempotent through `PRAGMA table_info`; preserve existing rows. Extend `capture(..., *, attempt_link: AttemptCaptureLink | None = None)` and keep queue/drop behavior unchanged. Capture failure logs and returns without altering the writer result; the durable attempt row remains the authoritative exact frame.

- [ ] **Step 7: Run dialogue/writer tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_setting_dialog.py tests/v2/test_proxy/test_writer.py tests/v2/test_capture_modules.py -q
```

Expected: PASS for FIFO correlation, exact deferred bytes, queue bounds, writer ordering/ownership, explicit outcomes, and capture links.

- [ ] **Step 8: Commit dialogue and writer boundaries**

```bash
git add addon/oig-proxy/proxy/dialog.py addon/oig-proxy/proxy/writer.py addon/oig-proxy/capture/frame_capture.py tests/v2/conftest.py tests/v2/test_setting_dialog.py tests/v2/test_proxy/test_writer.py tests/v2/test_capture_modules.py
git commit -m "feat: serialize local-setting dialogues and writes"
```

---

### Task 10: Route ONLINE and HYBRID Through Cloud-First Dialogue

**Files:**
- Modify: `addon/oig-proxy/proxy/server.py`
- Modify: `addon/oig-proxy/proxy/__init__.py`
- Modify: `addon/oig-proxy/protocol/frame.py`
- Create: `tests/v2/test_proxy/test_setting_dialog_online.py`
- Create: `tests/v2/test_proxy/test_setting_streams.py`
- Modify: `tests/v2/test_proxy/test_server.py`
- Modify: `tests/v2/test_integration.py`
- Modify: `tests/v2/conftest.py`

**Interfaces:**
- Consumes: `TwinCoordinator`, `SettingDialog`, `SerializedBoxWriter`, typed/validated frames, canonical capture, and existing mode selection.
- Produces: `ProxyConnectionContext`, single semantic event router, valid-device callback, cloud/ACK timers, cloud-first ONLINE/HYBRID behavior, and no replay hook for Tasks 11 through 13.

- [ ] **Step 1: Write cloud-priority and substitution tests**

Use in-process readers/writers and a real temporary store; assert raw byte arrays on both legs:

```python
@pytest.mark.asyncio
async def test_poll_reaches_cloud_and_queue_stays_pending_until_terminal_end(
    online_session: OnlineSessionHarness,
) -> None:
    poll = online_session.frame(result="IsNewSet", device_id="123")
    await online_session.box_send(poll)
    assert await online_session.cloud_read() == poll
    assert online_session.box_writes == []
    assert online_session.store.single_nonterminal().state is CommandState.PENDING


@pytest.mark.asyncio
async def test_cloud_setting_and_box_ack_round_trip_byte_exact_before_local_batch(
    online_session: OnlineSessionHarness,
) -> None:
    await online_session.open_isnewset_cycle()
    cloud_setting = online_session.frame(
        result="Setting", table="tbl_box_prms", item="MODE", value="1"
    )
    cloud_ack = online_session.frame(result="ACK", reason="Setting")
    await online_session.cloud_send(cloud_setting)
    assert await online_session.box_read() == cloud_setting
    await online_session.box_send(cloud_ack)
    assert await online_session.cloud_read() == cloud_ack
    assert online_session.store.single_nonterminal().state is CommandState.PENDING


@pytest.mark.asyncio
async def test_correlated_end_is_replaced_then_final_ack_returns_exact_end(
    online_session: OnlineSessionHarness,
) -> None:
    await online_session.open_isnewset_cycle()
    raw_end = online_session.frame(result="END", extra=b"<Marker>exact</Marker>")
    await online_session.cloud_send(raw_end)
    local_setting = await online_session.box_read()
    assert b"<Reason>Setting</Reason>" in local_setting
    assert local_setting != raw_end
    local_ack = online_session.frame(result="ACK", reason="Setting")
    await online_session.box_send(local_ack)
    assert await online_session.box_read() == raw_end
    assert online_session.cloud_received(local_ack) is False
    assert online_session.store.single_nonterminal().state is CommandState.AWAITING_EVENT


@pytest.mark.asyncio
async def test_local_ack_is_suppressed_and_does_not_publish_confirmed_state(
    online_session: OnlineSessionHarness,
) -> None:
    active = await online_session.begin_local_batch()
    ack = online_session.frame(result="ACK", reason="Setting")
    await online_session.box_send(ack)
    assert online_session.cloud_received(ack) is False
    assert online_session.confirmed_callbacks == []
    assert online_session.store.read_command(active.command_id).state is CommandState.AWAITING_EVENT


@pytest.mark.asyncio
async def test_only_exact_event_confirms_and_publishes_state(
    online_session: OnlineSessionHarness,
) -> None:
    command = await online_session.deliver_and_ack_local()
    await online_session.box_send(
        online_session.setting_event_for(command, new_value="3")
    )
    assert online_session.store.read_command(command.command_id).state is CommandState.AWAITING_EVENT
    assert online_session.confirmed_callbacks == []
    await online_session.box_send(
        online_session.setting_event_for(command, new_value=command.value_text)
    )
    assert online_session.store.read_command(command.command_id).state is CommandState.CONFIRMED
    assert len(online_session.confirmed_callbacks) == 1
```

Add no-eligible END pass-through, disabled pass-through, active owner elsewhere, multi-command next-Setting sequence, NACK final END/no retry, cloud NACK/no claim, and exact cloud END capture-on-receipt plus capture-on-forward tests.

- [ ] **Step 2: Write direction, framing, timeout, and ownership tests**

Cover non-substitution byte transparency and every connection-closing boundary:

```python
@pytest.mark.asyncio
async def test_disabled_or_empty_queue_is_byte_transparent_for_partial_and_coalesced_frames(
    transparent_session: OnlineSessionHarness,
) -> None:
    one = transparent_session.frame(result="ACK", reason="Other")
    two = transparent_session.frame(result="IsNewSet", device_id="123")
    await transparent_session.box_send_chunks((one[:9], one[9:] + two))
    assert await transparent_session.cloud_read_bytes(len(one + two)) == one + two


@pytest.mark.asyncio
async def test_wrong_session_ack_cannot_advance_local_command(
    two_session_harness: TwoSessionHarness,
) -> None:
    owner = await two_session_harness.session_a.begin_local_batch()
    ack = two_session_harness.frame(result="ACK", reason="Setting")
    await two_session_harness.session_b.box_send(ack)
    assert two_session_harness.store.read_command(owner.command_id).state is CommandState.AWAITING_ACK
    assert two_session_harness.session_b.closed is False


@pytest.mark.asyncio
async def test_invalid_crc_cannot_mutate_active_dialogue(
    online_session: OnlineSessionHarness,
) -> None:
    active = await online_session.begin_local_batch()
    corrupt = online_session.frame(result="ACK", reason="Setting")[:-12] + b"00000</CRC></Frame>\r\n"
    await online_session.box_send(corrupt)
    assert online_session.store.read_command(active.command_id).state in {
        CommandState.RETRY_PENDING, CommandState.FAILED,
    }
    assert online_session.cloud_received(corrupt) is False
    assert online_session.closed is True
```

Add tests for `IsNewFW`/`IsNewWeather`, invalid cloud CRC END forwarded without substitution, absolute 30-second cloud timeout, cloud EOF, invalid terminal, stale timer identity, partial EOF, exact 1 MiB and overflow, coalesced held BOX/cloud frames, unexpected active-dialogue BOX input, direct execution event, GetActual blocking, and no mid-dialog HYBRID fallback.

- [ ] **Step 3: Run ONLINE/HYBRID tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_proxy/test_setting_dialog_online.py tests/v2/test_proxy/test_setting_streams.py tests/v2/test_proxy/test_server.py tests/v2/test_integration.py -q
```

Expected: FAIL because current code injects before cloud, writes cloud bytes before correlation, drops coalesced frames, and lacks direction/session ownership.

- [ ] **Step 4: Build one per-connection semantic context and event queue**

Add:

```python
@dataclass(slots=True)
class ProxyConnectionContext:
    session_id: str
    route: SessionRoute
    dialog: SettingDialog
    box_assembler: FrameStreamAssembler
    cloud_assembler: FrameStreamAssembler
    box_writer: SerializedBoxWriter
    cloud_audit: CloudSettingAuditObserver
    semantic_events: asyncio.Queue[StreamEvent]
    cloud_writer: asyncio.StreamWriter | None
    close_requested: asyncio.Event
    cloud_timer: asyncio.TimerHandle | None = None
    ack_timer: asyncio.TimerHandle | None = None


@dataclass(frozen=True, slots=True)
class StreamFrameEvent:
    direction: FrameDirection
    frame: AssembledFrame
    registered_event: RegisteredEventToken | None = None


@dataclass(frozen=True, slots=True)
class StreamClosedEvent:
    direction: FrameDirection
    error_code: StreamErrorCode | None


StreamEvent = StreamFrameEvent | StreamClosedEvent
```

Create one UUID through `str(uuid.uuid4())` per connection; never use telemetry connection IDs for ownership. Freeze ONLINE/HYBRID versus OFFLINE before the first read. Construct `semantic_events` as `asyncio.Queue(maxsize=1)`: no unbounded frame/event backlog is permitted, each enqueued frame is independently capped at 1 MiB, and full-queue backpressure stops the read pump. Two read pumps assemble/timestamp frames and enqueue events; one router task is the sole dialogue mutator and semantic writer. The BOX pump performs only the extra race-barrier action required by Task 8: after CRC validation and strict event parsing, call `register_setting_event()` synchronously before the first `await` that follows complete-frame assembly and carry its token in `StreamFrameEvent`. The router later calls `handle_registered_event()`; no store mutation occurs in the pump. On cleanup, cancel pumps/timers, flush every registered event for the UUID and deliver any resulting confirmation callback, then commit retry/failure only if the owned attempt remains `awaiting_ack`; finally clear socket state, release writer ownership, and close both legs. A store failure while handling an unowned registered event leaves the raw ONLINE frame byte-transparent and enters Task 13's bounded recovery path. Never reinterpret an already-forwarded HYBRID request as OFFLINE after cloud failure.

- [ ] **Step 5: Implement BOX-to-cloud routing**

For a complete frame:

1. Capture raw BOX input and validate CRC before identity/command logic.
2. With no active local dialogue, forward structurally complete invalid-CRC ONLINE bytes unchanged; never bind identity or mutate store.
3. On a valid frame, parse metadata and bind the connection to its first non-empty device; reject a later identity change for local-control purposes. For every CRC-valid BOX frame carrying a non-empty device plus non-negative `ID` and `ID_Set`, invoke the valid-device callback before any possible claim so durable next counters advance past the latest observation.
4. On valid `IsNewSet`, require successful exact identity/counter observation before local eligibility, then open the FIFO cycle token before `cloud_writer.write(raw)` and `drain()`.
5. On a cloud-owned Setting ACK, forward raw bytes to cloud as a continuation and keep the FIFO head.
6. On an active local ACK/NACK, call the coordinator with exact direction/session/device/timestamp/raw evidence and never forward the local response to cloud.
7. On a BOX event without active local ownership, register/commit passive matching and then forward raw bytes unchanged. During active local ownership, do not forward it; direct confirmation closes the dialogue, while confirmation of older work first commits that evidence and then retries/fails the still-active attempt before close.
8. Hold later valid BOX requests while local semantic ownership is active; flush only after the deferred END drains.

The valid-device callback signature is:

```python
ValidDeviceCallback = Callable[[str, int | None, int | None], Awaitable[bool]]
```

- [ ] **Step 6: Implement cloud-to-BOX routing and local substitution**

Parse each cloud frame before writing it to BOX. A valid cloud Setting at the FIFO-head `IsNewSet` cycle is written byte-for-byte with purpose `CLOUD_FORWARD`, recorded through `CloudSettingAuditObserver.setting_forwarded()`, marks the cycle cloud-owned, and waits for its BOX ACK continuation. Its BOX ACK is recorded through `box_response_forwarded()` only after the same exact raw ACK is forwarded to cloud. A BOX execution event may be passed to `setting_event_observed()` for passive cloud telemetry, but cloud observation never calls the local store or confirmed-state callback. Close the observer's UUID session queue during connection cleanup. A valid correlated terminal END is captured on receipt and passed to `dialog.defer_correlated_terminal_end()`.

Before claiming, acquire writer dialogue ownership. Call `claim_and_write_next(..., trigger=CORRELATED_CLOUD_END)`. For `SENT`, begin the local attempt and arm its persisted ACK deadline. For `NO_ELIGIBLE`, `CONTROL_DISABLED`, `ACTIVE_DELIVERY_ELSEWHERE`, or `RENDER_FAILED`, release ownership and forward the exact END. For `WRITE_FAILED` or `WRITE_UNKNOWN`, discard the socket-only END and close.

On local ACK, commit `awaiting_event` plus optional successor preparation atomically. If the coordinator writes the successor, replace `dialog.active_attempt`; otherwise write the exact deferred END with purpose `DEFERRED_END`, close the cycle, flush held cloud frames to BOX in order, release ownership, then flush held BOX frames to cloud in order. On local NACK, send the exact deferred END and stop the batch. Capture every local Setting with its attempt link and every deferred END using identical raw bytes.

Invalid/unexpected cloud responses are forwarded as complete raw frames, taint the cycle, prohibit local substitution, and close both legs after available frames drain. Cloud EOF/failure is never an implicit END.

- [ ] **Step 7: Implement absolute cycle and exact attempt timers**

Arm the cloud timer from the monotonic deadline created when the poll is forwarded; never reset it on a cloud Setting. Arm the ACK wake-up from persisted wall-clock `ack_deadline_ms`. Timer callbacks carry expectation sequence or `(command_id, attempt_number, session_id, deadline)` and must recheck exact current identity before mutation. On timeout, commit retry/failure before releasing writer ownership and close the connection. Cancel both handles on normal cycle completion and cleanup.

Delete `_read_replay_frame_once()`, every `replay_setting_frame.xml` reference, pre-cloud injection, `IsNewFW`/`IsNewWeather` triggers, directionless `_handle_twin_frames`, key-only acknowledgement, ACK state publication, process-global cloud/local inflight state, and the immediate `_deliver_pending_for_isnewset` hook. Remove `extract_frame_from_buffer()` after the new pumps become its final callers.

- [ ] **Step 8: Run ONLINE/HYBRID tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_proxy/test_setting_dialog_online.py tests/v2/test_proxy/test_setting_streams.py tests/v2/test_proxy/test_server.py tests/v2/test_integration.py -q
```

Expected: PASS for cloud priority, byte identity, exact END substitution, local batch, direction/session evidence, frame preservation, timeouts, and replay removal.

- [ ] **Step 9: Commit cloud-first routing**

```bash
git add addon/oig-proxy/proxy/server.py addon/oig-proxy/proxy/__init__.py addon/oig-proxy/protocol/frame.py tests/v2/conftest.py tests/v2/test_proxy/test_setting_dialog_online.py tests/v2/test_proxy/test_setting_streams.py tests/v2/test_proxy/test_server.py tests/v2/test_integration.py
git commit -m "feat: enforce cloud-first setting dialogues"
```

---

### Task 11: Make OFFLINE Setting Dialogue Exactly Once

**Files:**
- Modify: `addon/oig-proxy/proxy/server.py`
- Modify: `addon/oig-proxy/proxy/local_ack.py`
- Create: `tests/v2/test_proxy/test_setting_dialog_offline.py`
- Modify: `tests/v2/test_local_ack.py`
- Modify: `tests/v2/test_integration.py`
- Modify: `tests/v2/test_proxy/test_server.py`

**Interfaces:**
- Consumes: Task 10 connection context/router, Task 8 coordinator, Task 9 writer, and canonical `build_end_time_frame()`.
- Produces: one OFFLINE application response decision per complete CRC-valid poll/ACK and parity capture consumed by Tasks 12 and 13.

- [ ] **Step 1: Write exactly-one OFFLINE response tests**

Create `tests/v2/test_proxy/test_setting_dialog_offline.py` with a writer that counts invocations, not only successful drains:

```python
@pytest.mark.asyncio
async def test_offline_isnewset_with_work_writes_exactly_one_setting(
    offline_session: OfflineSessionHarness,
) -> None:
    await offline_session.box_send(
        offline_session.frame(result="IsNewSet", device_id="123")
    )
    assert len(offline_session.writer.invocations) == 1
    assert b"<Reason>Setting</Reason>" in offline_session.writer.invocations[0]
    assert b"<Result>END</Result>" not in offline_session.writer.invocations[0]


@pytest.mark.asyncio
async def test_offline_isnewset_without_work_writes_exactly_one_end(
    empty_offline_session: OfflineSessionHarness,
) -> None:
    await empty_offline_session.box_send(
        empty_offline_session.frame(result="IsNewSet", device_id="123")
    )
    assert len(empty_offline_session.writer.invocations) == 1
    assert b"<Result>END</Result>" in empty_offline_session.writer.invocations[0]


@pytest.mark.asyncio
async def test_offline_final_local_ack_writes_exactly_one_end(
    offline_session: OfflineSessionHarness,
) -> None:
    active = await offline_session.begin_local_setting()
    offline_session.writer.clear()
    await offline_session.box_send(
        offline_session.frame(result="ACK", reason="Setting")
    )
    assert len(offline_session.writer.invocations) == 1
    assert b"<Result>END</Result>" in offline_session.writer.invocations[0]
    assert offline_session.store.read_command(active.command_id).state is CommandState.AWAITING_EVENT


@pytest.mark.asyncio
async def test_offline_unknown_write_never_attempts_second_response(
    offline_session: OfflineSessionHarness,
) -> None:
    offline_session.writer.fail_drain_once(ConnectionResetError("reset"))
    await offline_session.box_send(
        offline_session.frame(result="IsNewSet", device_id="123")
    )
    assert len(offline_session.writer.invocations) == 1
    assert offline_session.closed is True
```

Add work/no-work, active owner elsewhere, ACK with successor, final ACK, NACK, claim/render/store failure, invalid CRC, uncorrelated ACK/NACK, and session UUID correlation cases.

- [ ] **Step 2: Write negative trigger and parity tests**

Add explicit non-setting and capture assertions:

```python
@pytest.mark.asyncio
async def test_firmware_weather_and_unrelated_frames_never_claim(
    offline_session: OfflineSessionHarness,
) -> None:
    for result in ("IsNewFW", "IsNewWeather", "ACK"):
        offline_session.writer.clear()
        await offline_session.box_send(offline_session.frame(result=result))
        assert len(offline_session.writer.invocations) <= 1
        assert all(b"<Reason>Setting</Reason>" not in raw for raw in offline_session.writer.invocations)
    assert offline_session.store.single_nonterminal().state is CommandState.PENDING


@pytest.mark.asyncio
async def test_offline_setting_capture_has_exact_attempt_identity(
    offline_session: OfflineSessionHarness,
) -> None:
    attempt = await offline_session.begin_local_setting()
    captured = offline_session.capture.fetch_latest_for_attempt(
        attempt.command_id, attempt.attempt_number
    )
    assert captured.direction == "proxy_to_box"
    assert captured.raw_bytes == attempt.wire_frame
    assert captured.audit_id == attempt.audit_id
```

Assert a valid uncorrelated ACK receives only the existing one generic OFFLINE response and does not mutate a command. Assert malformed/invalid-CRC input never calls the coordinator; it may receive only the canonical existing error response, outside the complete-valid exactly-one guarantee.

- [ ] **Step 3: Run OFFLINE tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_proxy/test_setting_dialog_offline.py tests/v2/test_local_ack.py tests/v2/test_integration.py tests/v2/test_proxy/test_server.py -q
```

Expected: FAIL because current OFFLINE handling writes generic END/ACK before a second Setting and drops session correlation.

- [ ] **Step 4: Implement one OFFLINE decision function**

Add a return type that makes a second response impossible:

```python
class OfflineDecisionKind(str, Enum):
    LOCAL_SETTING = "local_setting"
    SYNTHESIZED_END = "synthesized_end"
    GENERIC_RESPONSE = "generic_response"
    CLOSE_WITHOUT_SECOND_WRITE = "close_without_second_write"


@dataclass(frozen=True, slots=True)
class OfflineResponseDecision:
    kind: OfflineDecisionKind
    frame: bytes | None
    active_attempt: ActiveLocalAttempt | None
```

For each complete validated BOX frame, classify once before invoking any response writer. A valid `IsNewSet` with an eligible claim chooses one local Setting; no work, disabled control, active owner elsewhere, or pre-invocation render/store failure chooses one synthesized END from `build_end_time_frame()`. A correlated local ACK chooses the already-prepared successor Setting or one END. A correlated NACK commits terminal failure and chooses one END. An uncorrelated ACK/NACK chooses only the existing canonical generic response and never selects work.

Pass the same connection UUID, exact direction, device, and receipt timestamp used by ONLINE. Acquire semantic writer ownership around an OFFLINE local batch. Once `write_frame()` or `write_attempt()` is invoked, set `response_invoked=True`; any failed/unknown result closes without invoking an END fallback. Never send an ACK in response to a correlated local ACK.

- [ ] **Step 5: Remove the legacy OFFLINE injection path**

Delete the old `_handle_offline_frames` ordering that calls `build_local_response()` before local delivery, the immediate `_handle_twin_frames` setting hook, the duplicate OFFLINE Setting serializer, and random OFFLINE wire IDs. Route `IsNewFW`, `IsNewWeather`, table uploads, generic ACKs, and invalid frames only through the canonical non-setting response branch. Capture local attempts through `SerializedBoxWriter` with the same purpose/link as ONLINE.

- [ ] **Step 6: Run OFFLINE tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_proxy/test_setting_dialog_offline.py tests/v2/test_local_ack.py tests/v2/test_integration.py tests/v2/test_proxy/test_server.py -q
```

Expected: PASS with one writer invocation per complete valid poll/ACK, no unrelated trigger, exact session correlation, and ONLINE/OFFLINE attempt parity.

- [ ] **Step 7: Commit OFFLINE routing**

```bash
git add addon/oig-proxy/proxy/server.py addon/oig-proxy/proxy/local_ack.py tests/v2/test_proxy/test_setting_dialog_offline.py tests/v2/test_local_ack.py tests/v2/test_integration.py tests/v2/test_proxy/test_server.py
git commit -m "feat: make offline setting responses transactional"
```

---

### Task 12: Enforce Retain-Aware Exact-Device MQTT Ingress

**Files:**
- Modify: `addon/oig-proxy/mqtt/client.py`
- Rewrite: `addon/oig-proxy/twin/handler.py`
- Modify: `addon/oig-proxy/twin/store.py`
- Modify: `addon/oig-proxy/twin/state.py`
- Modify: `tests/v2/test_mqtt/test_client.py`
- Rewrite: `tests/v2/test_twin_handler.py`
- Modify: `tests/v2/conftest.py`

**Interfaces:**
- Consumes: Task 4 value/XML validation, Task 7 durable ingress methods, exact known device ID, and Task 3 startup gate.
- Produces: retain-aware `MQTTMessageCallback`, reconnect-safe exact subscriptions, and `TwinControlHandler` consumed by Task 13.

- [ ] **Step 1: Write MQTT callback and reconnect tests**

Extend the MQTT client suite with exact retain and registry behavior:

```python
def test_on_message_passes_retain_metadata(mqtt_client: MQTTClient) -> None:
    received: list[tuple[str, bytes, bool]] = []
    assert mqtt_client.subscribe("oig/123/control/set", lambda *args: received.append(args))
    message = SimpleNamespace(
        topic="oig/123/control/set", payload=b"payload", retain=True
    )
    mqtt_client._on_message(None, None, message)
    assert received == [("oig/123/control/set", b"payload", True)]


def test_on_connect_restores_registered_exact_subscriptions(
    mqtt_client: MQTTClient, paho_client: Mock
) -> None:
    mqtt_client.subscribe("oig/123/control/set", Mock())
    mqtt_client.subscribe("oig_local/123/set/#", Mock())
    paho_client.subscribe.reset_mock()
    mqtt_client._on_connect(paho_client, None, None, 0)
    assert paho_client.subscribe.call_args_list == [
        call("oig/123/control/set", qos=mqtt_client.qos),
        call("oig_local/123/set/#", qos=mqtt_client.qos),
    ]


def test_unsubscribe_while_disconnected_prevents_restore(mqtt_client: MQTTClient) -> None:
    mqtt_client.subscribe("oig/123/control/set", Mock())
    mqtt_client._connected = False
    assert mqtt_client.unsubscribe("oig/123/control/set") is True
    assert "oig/123/control/set" not in mqtt_client.registered_subscriptions
```

Add initial subscribe failure not registered, callback exception isolation, and `health_check_loop(..., on_ready=...)` invocation only after an initial-failure reconnect succeeds.

- [ ] **Step 2: Write exact ingress and rejection-audit tests**

Use a real temporary store and a fake MQTT transport:

```python
@pytest.mark.asyncio
async def test_retained_message_is_rejected_before_json_parse_and_enqueue(
    handler: TwinControlHandler, store: TwinCommandStore
) -> None:
    await handler.handle_message(
        "oig/123/control/set", b"not-json", True, received_at_ms=100
    )
    ingress = store.read_latest_ingress()
    assert ingress.disposition is IngressDisposition.REJECTED_RETAINED
    assert store.status_snapshot(device_id="123").nonterminal_commands == 0


@pytest.mark.asyncio
async def test_retained_control_creates_ingress_audit_only(
    handler: TwinControlHandler, store: TwinCommandStore
) -> None:
    before_transitions = store.read_transitions()
    await handler.handle_message(
        "oig/123/control/set",
        b'{"table":"tbl_box_prms","key":"MODE","value":2}',
        True,
        received_at_ms=101,
    )
    ingress = store.read_latest_ingress()
    assert ingress.disposition is IngressDisposition.REJECTED_RETAINED
    assert ingress.command_id is None and ingress.audit_id is None
    assert store.read_transitions() == before_transitions


@pytest.mark.asyncio
async def test_exact_device_json_command_links_ingress_and_command(
    handler: TwinControlHandler, store: TwinCommandStore
) -> None:
    await handler.handle_message(
        "oig/123/control/set",
        b'{"device_id":"123","table":"tbl_box_prms","key":"MODE","value":2}',
        False,
        received_at_ms=100,
    )
    command = store.single_nonterminal()
    ingress = store.read_latest_ingress()
    assert (command.device_id, command.table_name, command.item_name, command.value_text) == (
        "123", "tbl_box_prms", "MODE", "2"
    )
    assert (ingress.command_id, ingress.audit_id) == (
        command.command_id, command.audit_id
    )


@pytest.mark.asyncio
async def test_unknown_device_refuses_subscription_and_enqueue(
    mqtt: FakeMQTT, store: TwinCommandStore, event_loop: asyncio.AbstractEventLoop
) -> None:
    handler = TwinControlHandler(
        mqtt=mqtt, store=store, device_id="", control_enabled=True,
        loop=event_loop,
    )
    assert await handler.start() is False
    assert mqtt.subscriptions == []
    await handler.handle_message(
        "oig/123/control/set", b"{}", False, received_at_ms=100
    )
    assert store.read_latest_ingress().disposition is IngressDisposition.REJECTED_UNKNOWN_DEVICE
```

Add disabled no-subscribe, wrong topic device, optional JSON `device_id` mismatch, exact five-segment compatibility topic, extra compatibility segments, 16 KiB boundary/one byte over, strict UTF-8, malformed JSON/constants, wrong field types, non-allowlisted target, Decimal rejection, forbidden XML, retained proxy control before dispatch, proxy-control accepted ingress without command IDs, subscription rollback, stop/unsubscribe/task drain, and unavailable-store rejection without dispatch.

- [ ] **Step 3: Run MQTT/handler tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_mqtt/test_client.py tests/v2/test_twin_handler.py -q
```

Expected: FAIL because callbacks omit retain, subscriptions use wildcard device segments, and ingress mutates an in-memory queue.

- [ ] **Step 4: Extend the MQTT callback contract and registry**

Use:

```python
MQTTMessageCallback = Callable[[str, bytes, bool], None]


def subscribe(self, topic: str, callback: MQTTMessageCallback) -> bool:
    result, _ = self._client.subscribe(topic, qos=self.qos)
    if result != mqtt.MQTT_ERR_SUCCESS:
        return False
    self._subscriptions[topic] = callback
    return True


def unsubscribe(self, topic: str) -> bool:
    self._subscriptions.pop(topic, None)
    if not self._connected:
        return True
    result, _ = self._client.unsubscribe(topic)
    return result == mqtt.MQTT_ERR_SUCCESS


@property
def registered_subscriptions(self) -> frozenset[str]:
    return frozenset(self._subscriptions)
```

`_on_message()` passes `str(msg.topic)`, immutable `bytes(msg.payload)`, and `bool(msg.retain)`. `_on_connect()` re-subscribes every registered topic in sorted order. Only register after initial broker success; remove registry state even while disconnected. Extend the health loop with optional awaited `on_ready` after recovery from initial failure; ordinary reconnect needs no handler callback because `_on_connect()` restores registered topics.

- [ ] **Step 5: Implement exact-device handler lifecycle**

Use this constructor and public methods:

```python
class TwinControlHandler:
    def __init__(
        self,
        *,
        mqtt: MQTTClient,
        store: TwinCommandStore,
        device_id: str,
        control_enabled: bool,
        loop: asyncio.AbstractEventLoop,
        namespace: str = "oig_local",
        proxy_control_handler: Callable[[str, str, str], bool] | None = None,
        audit_publisher: SettingsAuditPublisher | None = None,
    ) -> None: ...

    async def start(self) -> bool: ...
    async def stop(self) -> None: ...
    async def handle_message(
        self,
        topic: str,
        payload: bytes,
        retain: bool,
        *,
        received_at_ms: int,
    ) -> None: ...
```

Validate device segments as non-empty and free of `/`, `+`, `#`, and NUL. Subscribe only to `oig/{device_id}/control/set` and `{namespace}/{device_id}/set/#`; roll back the first if the second fails. The Paho callback captures `time.time_ns() // 1_000_000` and schedules `handle_message()` onto the application loop; SQLite never runs on the Paho thread. Stop unsubscribes both and awaits handler-owned tasks.

- [ ] **Step 6: Implement bounded disposition before command creation**

Create `ControlIngress(uuid4, received_at_ms, bounded topic/device/retain/raw)` before every branch. Produce `raw_text` without JSON interpretation by decoding at most the first 16,384 payload bytes with `errors="backslashreplace"`; the later strict UTF-8 step still decides acceptance. Apply this exact order:

1. Reject disabled, retained, unknown/unsafe bound device, wrong actual topic, and payload length above `16_384` before decoding.
2. Decode strict UTF-8; never log raw payload at INFO.
3. For JSON, use `json.loads(text, parse_int=Decimal, parse_float=Decimal, parse_constant=reject_constant)`; require string `table/key`, non-null `value`, and optional string `device_id` equal to topic and bound device.
4. For compatibility topics, require exactly `{namespace}/{device}/set/{table}/{key}`; the whole UTF-8 payload is the value.
5. Enforce allowlist, Task 4 canonical Decimal result, and XML 1.0 text.
6. For `proxy_control`, atomically record `ACCEPTED_PROXY_CONTROL` with null command/audit links before invoking its callback.
7. For wire targets, call `enqueue_command()` so accepted ingress, supersession, command, and transitions share one transaction; publish every returned committed snapshot in transition-ID order.
8. On store failure, emit one bounded emergency counter/log and reject without enqueue or proxy dispatch.

Every rejection calls `record_ingress_disposition()` and never creates a command transition. Bound topic/reason/error fields to 1 KiB and raw ingress to 16 KiB.

- [ ] **Step 7: Run MQTT/handler tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_mqtt/test_client.py tests/v2/test_twin_handler.py -q
```

Expected: PASS for retain-first rejection, exact identity, durable audit, Decimal values, reconnect restoration, and task-safe handler lifecycle.

- [ ] **Step 8: Commit MQTT ingress**

```bash
git add addon/oig-proxy/mqtt/client.py addon/oig-proxy/twin/handler.py addon/oig-proxy/twin/store.py addon/oig-proxy/twin/state.py tests/v2/conftest.py tests/v2/test_mqtt/test_client.py tests/v2/test_twin_handler.py
git commit -m "feat: bind MQTT control ingress to exact device"
```

---

### Task 13: Wire Startup Gate, Discovery Cleanup, and Confirmed State

**Files:**
- Modify: `addon/oig-proxy/mqtt/client.py`
- Modify: `addon/oig-proxy/main.py`
- Modify: `addon/oig-proxy/device_id.py`
- Modify: `addon/oig-proxy/proxy/server.py`
- Modify: `addon/oig-proxy/sensor/processor.py`
- Modify: `addon/oig-proxy/twin/state.py`
- Modify: `addon/oig-proxy/twin/delivery.py`
- Modify: `addon/oig-proxy/twin/__init__.py`
- Modify: `tests/v2/test_mqtt/test_client.py`
- Rewrite: `tests/v2/test_main_integration.py`
- Modify: `tests/v2/test_device_id.py`
- Modify: `tests/v2/test_frame_processor.py`
- Modify: `tests/v2/test_integration.py`

**Interfaces:**
- Consumes: All production boundaries from Tasks 3 through 12.
- Produces: complete `ProxyApp` lifecycle, enabled/disabled discovery, exact handler reconciliation, `ConfirmedSetting` publication, passive evidence while disabled, and removal of legacy queue/delivery symbols.

- [ ] **Step 1: Write control discovery enable/cleanup tests**

Assert read-only discovery plus deterministic tombstones when disabled:

```python
def test_disabled_control_publishes_read_only_sensor_and_all_control_tombstones(
    mqtt_client: MQTTClient, paho_client: Mock
) -> None:
    mqtt_client.control_enabled = False
    assert mqtt_client.send_discovery("123", "tbl_box_prms", "MODE", {}) is True
    calls = paho_client.publish.call_args_list
    sensor_topic = "homeassistant/sensor/oig_123_tbl_box_prms_MODE/config"
    assert any(call.args[0] == sensor_topic and call.args[1] for call in calls)
    control_tombstones = [
        call for call in calls
        if call.args[0].startswith((
            "homeassistant/number/", "homeassistant/switch/", "homeassistant/select/"
        )) and call.args[1] == b""
    ]
    assert len(control_tombstones) == 3
    assert all(call.kwargs == {"qos": 1, "retain": True} for call in control_tombstones)


def test_control_tombstone_cleanup_covers_every_allowlisted_target(
    mqtt_client: MQTTClient, paho_client: Mock
) -> None:
    assert mqtt_client.publish_control_discovery_tombstones("123") is True
    tombstone_topics = {
        call.args[0] for call in paho_client.publish.call_args_list
        if call.args[1] == b""
    }
    expected = {
        f"homeassistant/{component}/{entity_id}/config"
        for table, keys in CONTROL_WRITE_WHITELIST.items()
        for key in keys
        for component in ("number", "switch", "select")
        for entity_id in [mqtt_client.control_unique_id("123", table, key)]
    }
    assert tombstone_topics == expected
```

Add enabled one-command-entity plus two stale-component tombstones, per-publish failure continuation/aggregate false, sorted target order, reconnect cleanup for every known identity, and no cleanup for `"unknown"`/empty IDs.

- [ ] **Step 2: Write startup, reconnect, identity, and shutdown tests**

Use ordered mocks and temporary store paths. Any `known_device_id="123"` fixture that expects an immediate subscription must seed the store with one prior CRC-valid counter observation; a persisted ID without that device row must not subscribe until `_on_valid_device_identity()` observes both counters.

```python
@pytest.mark.asyncio
async def test_startup_disabled_recovers_store_without_handler_or_local_write(
    app_factory: AppFactory, seeded_store_path: Path
) -> None:
    app = app_factory(
        control_mqtt_enabled=False, twin_db_path=str(seeded_store_path)
    )
    assert await app.startup() is True
    assert app.control_recovery_report is not None
    assert app.twin_handler is None
    assert app.mqtt.control_enabled is False
    assert app.proxy.local_setting_write_count == 0
    await app.shutdown()


@pytest.mark.asyncio
async def test_store_recovers_before_control_subscription(
    app_factory: AppFactory, ordered_events: list[str]
) -> None:
    app = app_factory(control_mqtt_enabled=True, known_device_id="123")
    app.store_open_hook = lambda: ordered_events.append("store-open")
    app.store_recover_hook = lambda: ordered_events.append("store-recover")
    app.subscription_hook = lambda _: ordered_events.append("subscribe")
    await app.startup()
    assert ordered_events.index("store-recover") < ordered_events.index("subscribe")
    await app.shutdown()


@pytest.mark.asyncio
async def test_crc_valid_first_device_reconciles_exact_handler(app: ProxyApp) -> None:
    assert app.twin_handler is None
    accepted = await app._on_valid_device_identity("123", 14000000, 1786000000)
    assert accepted is True
    assert app.twin_handler is not None
    assert app.mqtt.registered_subscriptions == {
        "oig/123/control/set", f"{app.config.mqtt_namespace}/123/set/#"
    }


@pytest.mark.asyncio
async def test_unknown_device_poll_and_control_cannot_claim(
    unknown_device_runtime: tuple[ProxyApp, InProcessBoxClient],
) -> None:
    app, box = unknown_device_runtime
    assert app.device_id_manager.device_id is None
    assert app.twin_handler is None
    await box.send(crc_valid_frame(result="IsNewSet", device_id=""))
    assert app.proxy.local_setting_write_count == 0
    assert app.twin_handler is None


@pytest.mark.asyncio
async def test_shutdown_stops_handler_and_sweeper_before_store_close(
    started_app: ProxyApp, ordered_events: list[str]
) -> None:
    instrument_shutdown(started_app, ordered_events)
    await started_app.shutdown()
    assert ordered_events.index("handler-stop") < ordered_events.index("store-close")
    assert ordered_events.index("sweeper-stop") < ordered_events.index("store-close")
    assert ordered_events.index("store-close") < ordered_events.index("mqtt-disconnect")
```

Seed the disabled-startup fixture with one non-overdue `pending`, one `retry_pending`, one `awaiting_ack` below the limit, one non-overdue `awaiting_event`, and separate overdue pending/event rows. In `test_startup_disabled_recovers_store_without_handler_or_local_write`, assert recovery maps the recovered `awaiting_ack` to `retry_pending`, expires/incompletes only the overdue rows, preserves the non-overdue rows and all stable identities, creates no handler/subscription/control entity, emits all tombstones, and writes no local Setting across valid ONLINE and OFFLINE `IsNewSet` polls. Then submit an exact event for the preserved `awaiting_event` row and assert passive confirmation succeeds once without a control write. Add enabled known/unknown device, persisted identity revalidation, mismatch no retarget, initial MQTT failure then `on_ready` reconciliation, store failure no handler, three bounded open attempts, startup warnings once, disabled tombstone cleanup, and save failure no in-memory identity mutation.

- [ ] **Step 3: Write confirmed-state-only publication tests**

Replace ACK-confirmed expectations:

```python
@pytest.mark.asyncio
async def test_ack_never_calls_confirmed_setting_callback(
    proxy_harness: ProxyHarness,
) -> None:
    await proxy_harness.begin_local_setting()
    await proxy_harness.box_send(proxy_harness.frame(result="ACK", reason="Setting"))
    assert proxy_harness.confirmed_callbacks == []


@pytest.mark.asyncio
async def test_committed_exact_event_calls_callback_once(
    proxy_harness: ProxyHarness,
) -> None:
    command = await proxy_harness.acknowledged_local_setting()
    await proxy_harness.box_send(proxy_harness.exact_event_for(command))
    await proxy_harness.box_send(proxy_harness.exact_event_for(command))
    assert proxy_harness.confirmed_callbacks == [
        ConfirmedSetting(
            command.command_id, command.audit_id,
            proxy_harness.event_evidence_id, command.device_id,
            command.table_name, command.item_name, command.value_text,
            proxy_harness.event_received_at_ms,
        )
    ]


@pytest.mark.asyncio
async def test_failed_mqtt_publish_does_not_advance_confirmed_merge_cache(
    frame_processor: FrameProcessor, mqtt_client: Mock
) -> None:
    mqtt_client.publish_state.return_value = False
    before = frame_processor.state_snapshot("123", "tbl_box_prms")
    result = await frame_processor.publish_confirmed_setting(
        device_id="123", table="tbl_box_prms", key="MODE", value="2"
    )
    assert result is False
    assert frame_processor.state_snapshot("123", "tbl_box_prms") == before
```

Add NACK/retry/timeout/incomplete/unmatched/duplicate no callback, one-key merge preserving existing table keys, successful publish then cache update, and publish failure not rolling back committed command.

- [ ] **Step 4: Run lifecycle/discovery/state tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_mqtt/test_client.py tests/v2/test_main_integration.py tests/v2/test_device_id.py tests/v2/test_frame_processor.py tests/v2/test_integration.py -q
```

Expected: FAIL because main constructs the legacy queue, ignores the gate, starts unknown wildcard control, and publishes state on ACK.

- [ ] **Step 5: Implement control discovery lifecycle**

Add `control_enabled: bool = False` to `MQTTClient.__init__`. Track known normalized device IDs. `send_discovery()` must always publish normal sensor telemetry discovery; when enabled, publish the one mapped command component and zero-length retained tombstones for the other two components; when disabled, publish zero-length retained tombstones for all `number/switch/select` variants. `publish_control_discovery_tombstones(device_id)` iterates sorted `(table,key,component)`, continues after failure, and returns true only when all publishes succeed. On reconnect while disabled, repeat cleanup for the connect identity plus every known non-unknown identity.

Use deterministic command mapping: `proxy_control/PROXY_MODE` and `tbl_box_prms/MODE` are selects; explicit zero/one boolean-alias constraints are switches; every other target is a number.

- [ ] **Step 6: Replace startup and handler reconciliation**

Replace `twin_queue/twin_delivery` fields with:

```python
self.twin_store: TwinCommandStore | None = None
self.twin_coordinator: TwinCoordinator | None = None
self.twin_handler: TwinControlHandler | None = None
self.control_recovery_report: RecoveryReport | None = None
self._control_reconcile_lock = asyncio.Lock()
self._deadline_task: asyncio.Task[None] | None = None
```

Startup order is exact:

1. Load persisted device identity.
2. Construct store from `config.twin_db_path` and `ControlPolicy` milliseconds.
3. Attempt open/recovery at offsets `0.0`, `1.0`, and `2.0` seconds; assign the committed result to `control_recovery_report`. If all fail, preserve the database, leave that property null, expose degraded status, create no coordinator/handler/network subscription, return startup failure, and let the Home Assistant supervisor restart the process. Never rename, delete, truncate, or recreate the failed file between attempts.
4. Create `SettingsAuditPublisher` and `TwinCoordinator(control_enabled=config.control_mqtt_enabled)` only after successful recovery.
5. Connect MQTT with persisted identity or the proxy status identity; never use literal `"unknown"` for control discovery/subscriptions.
6. Create `FrameProcessor`; publish sensor discovery and enabled command discovery or disabled tombstones for a persisted identity.
7. Reconcile the exact handler only when the gate is enabled, store/coordinator recovered, MQTT is ready, identity is known, and `read_device()` proves durable counters were observed. A fresh schema beside an older persisted device-ID file therefore waits for the first CRC-valid frame carrying both counters; later restarts may use the already-durable device row.
8. Start proxy with coordinator, valid-device callback, confirmed-setting callback, and serialized capture adapter.
9. Start deadline sweeper even when control is disabled so pending/event deadlines advance and passive exact evidence remains matchable.
10. Pass `_on_mqtt_ready` to the health loop for recovery after initial connect failure.

`DeviceIdManager` is the sole durable binding authority; the SQLite `devices` table owns counters/history but never selects or repairs the bound identity. On startup, use only a valid normalized persisted ID; when the file is absent/corrupt, remain unknown even if SQLite contains one or more device rows. Ignore SQLite rows for other IDs. `_reconcile_control_handler()` runs under its lock, compares the exact desired identity, stops a stale handler before replacement, and never retargets after mismatch. `_on_valid_device_identity(device_id, observed_id: int | None, observed_id_set: int | None)` is called only by Task 10 after CRC validation; reject a mismatch even when counters are absent. For the first identity, atomically persist the file before changing the in-memory binding or writing SQLite; persistence failure leaves all three unchanged. After a successful new binding, or for an existing exact binding, call `store.observe_device()` when both counters are present so every valid observation advances durable next values. An observation-store failure leaves the persisted binding intact but creates no handler because `read_device()` is not ready; it enters the bounded store recovery policy. An `IsNewSet` poll without both counters remains ineligible for local claim. Update telemetry/discovery and reconcile only for the exact persisted identity. Generic `_on_frame()` must not establish control identity.

On a runtime store mutation failure, the coordinator marks itself unavailable, the active local dialogue closes without forwarding proxy-owned evidence, and the exact handler stops. Unowned ONLINE frames continue transparently while a bounded recovery burst runs at offsets `0.0`, `1.0`, and `2.0` seconds. Resume local control only if lock, pragma readback, schema, and full deadline reconciliation all succeed; otherwise set the application stop event for supervisor restart after the current router cleanup. Tests must prove an unowned frame forwards unchanged during the recovery burst and no local Setting is written.

- [ ] **Step 7: Implement committed confirmation publication and shutdown**

Change the callback to:

```python
async def _on_confirmed_setting(self, confirmation: ConfirmedSetting) -> None:
    if self.frame_processor is None:
        return
    published = await self.frame_processor.publish_confirmed_setting(
        device_id=confirmation.device_id,
        table=confirmation.table_name,
        key=confirmation.item_name,
        value=confirmation.value_text,
    )
    if not published:
        logger.warning(
            "confirmed setting state publish failed command_id=%s evidence_id=%s",
            confirmation.command_id,
            confirmation.evidence_id,
        )
```

The proxy calls it only when the committed coordinator `EventMatchResult.confirmation` is non-null. Keep generic event processing for observability. `FrameProcessor.publish_confirmed_setting()` merges one canonical value into cached table state, publishes through MQTT, and updates its merge cache only on success.

Shutdown order: stop new proxy sessions; stop exact handler; cancel deadline/health tasks; let coordinator release exact owned attempts; close store; stop capture/telemetry/status; disconnect MQTT. Log configuration `startup_warnings` exactly once immediately after `configure_logging()` in `main()`.

- [ ] **Step 8: Remove transitional legacy symbols and behavior**

Delete `TwinQueue`, legacy `TwinSetting`, legacy `TwinDelivery`, process-global inflight/cloud-pending dictionaries, duplicate Setting builder, key-only ACK methods, old queue telemetry consumer, wildcard handler constructor, and their exports. Update all imports and test fixtures to the durable types. Add a source assertion that these identifiers are absent:

```python
def test_legacy_local_setting_paths_are_removed() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (TWIN_STATE, TWIN_DELIVERY, PROXY_SERVER)
    )
    for forbidden in (
        "class TwinQueue", "class TwinDelivery", "_inflight_key",
        "replay_setting_frame.xml", "build_setting_xml",
    ):
        assert forbidden not in source
```

- [ ] **Step 9: Run lifecycle/discovery/state tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_mqtt/test_client.py tests/v2/test_main_integration.py tests/v2/test_device_id.py tests/v2/test_frame_processor.py tests/v2/test_integration.py -q
```

Expected: PASS for hard gate, exact lifecycle, retained discovery cleanup, degraded store behavior, reconnect, passive confirmation, and state publication only after exact event.

- [ ] **Step 10: Run the full non-E2E suite after legacy removal**

Run:

```bash
.venv/bin/python -m pytest tests/v2 -m "not e2e" -q
```

Expected: PASS; no test retains ACK-as-confirmed, in-memory overwrite, replay injection, wildcard-device, or pre-cloud assumptions.

- [ ] **Step 11: Commit complete runtime wiring**

```bash
git add addon/oig-proxy/mqtt/client.py addon/oig-proxy/main.py addon/oig-proxy/device_id.py addon/oig-proxy/proxy/server.py addon/oig-proxy/sensor/processor.py addon/oig-proxy/twin tests/v2/test_mqtt/test_client.py tests/v2/test_main_integration.py tests/v2/test_device_id.py tests/v2/test_frame_processor.py tests/v2/test_integration.py
git commit -m "feat: bind local control to runtime lifecycle"
```

---

### Task 14: Prove the Transaction Through Loopback Fake Endpoints

**Files:**
- Create: `tests/v2/e2e/conftest.py`
- Create: `tests/v2/e2e/fakes.py`
- Create: `tests/v2/e2e/test_local_setting_transaction.py`
- Modify: `tests/v2/egress_guard.py`
- Modify: `tests/v2/conftest.py`

**Interfaces:**
- Consumes: The real `ProxyServer`, store, coordinator, handler, serializer, MQTT/capture interfaces, and Task 2 guard.
- Produces: all E2E nodes fixed in Task 1 plus `reports/egress-guard.json` evidence consumed by Task 15 and the PR.

- [ ] **Step 1: Create explicit fake transport contracts**

In `tests/v2/e2e/fakes.py`, define no-production-default fakes:

```python
class FakeMQTTTransport:
    def __init__(self) -> None:
        self.ready = True
        self.subscriptions: dict[str, MQTTMessageCallback] = {}
        self.published: list[PublishedMessage] = []

    def subscribe(self, topic: str, callback: MQTTMessageCallback) -> bool:
        self.subscriptions[topic] = callback
        return True

    def unsubscribe(self, topic: str) -> bool:
        self.subscriptions.pop(topic, None)
        return True

    def emit(self, topic: str, payload: bytes, *, retain: bool = False) -> None:
        self.subscriptions[topic](topic, payload, retain)

    def publish(self, topic: str, payload: bytes | str, *, qos: int, retain: bool) -> bool:
        self.published.append(PublishedMessage(topic, payload, qos, retain))
        return True


@dataclass(slots=True)
class LocalControlHarness:
    db_path: Path
    fake_mqtt: FakeMQTTTransport
    fake_cloud: FakeCloudEndpoint
    fake_box: FakeBoxEndpoint
    store: TwinCommandStore
    coordinator: TwinCoordinator
    handler: TwinControlHandler
    proxy: ProxyServer
    capture: FrameCapture

    async def start(self, *, control_enabled: bool = True) -> None: ...
    async def restart_proxy_and_store(self) -> None: ...
    async def stop(self) -> None: ...
```

`FakeCloudEndpoint` must bind only `127.0.0.1` on port `0`, expose exact received/sent byte sequences, and script delays/EOF. `FakeBoxEndpoint` connects only to the actual loopback proxy port and supports split/coalesced sends. Inject fake MQTT, fake telemetry sink, temporary frame capture, and temporary DB explicitly; never construct a Paho connection or production host transport.

- [ ] **Step 2: Write cloud-priority, ACK/event, and same-key E2E tests**

Add the fixed Task 1 node names:

```python
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.e2e,
    pytest.mark.enable_socket,
    pytest.mark.local_control,
]


async def test_e2e_online_cloud_priority_then_local_batch(
    harness: LocalControlHarness,
) -> None:
    await harness.enqueue("tbl_box_prms", "MODE", "2")
    await harness.fake_box.send(harness.isnewset_poll())
    assert await harness.fake_cloud.read_frame() == harness.last_poll
    cloud_setting = harness.cloud_setting("tbl_box_prms", "MODE", "1")
    await harness.fake_cloud.send(cloud_setting)
    assert await harness.fake_box.read_frame() == cloud_setting
    cloud_ack = harness.setting_ack()
    await harness.fake_box.send(cloud_ack)
    assert await harness.fake_cloud.read_frame() == cloud_ack
    raw_end = harness.cloud_end(marker="exact-end")
    await harness.fake_cloud.send(raw_end)
    local_setting = await harness.fake_box.read_frame()
    assert b"<NewValue>2</NewValue>" in local_setting
    await harness.fake_box.send(harness.setting_ack())
    assert await harness.fake_box.read_frame() == raw_end


async def test_e2e_ack_is_delivery_only_until_exact_event(
    harness: LocalControlHarness,
) -> None:
    command = await harness.deliver_and_ack("tbl_box_prms", "MODE", "2")
    assert command.state is CommandState.AWAITING_EVENT
    assert harness.confirmed_state_messages() == []
    await harness.fake_box.send(harness.setting_event(command, new_value="2"))
    assert harness.store.read_command(command.command_id).state is CommandState.CONFIRMED
    assert harness.confirmed_state_messages()[-1].value_for("MODE") == "2"


async def test_e2e_rapid_same_key_updates_do_not_overwrite_attempted_command(
    harness: LocalControlHarness,
) -> None:
    sent = await harness.begin_local_delivery("tbl_box_prms", "MODE", "1")
    await harness.enqueue("tbl_box_prms", "MODE", "2")
    await harness.enqueue("tbl_box_prms", "MODE", "3")
    assert harness.store.read_command(sent.command_id).value_text == "1"
    assert harness.store.same_target_states() == [
        ("1", CommandState.AWAITING_ACK),
        ("2", CommandState.SUPERSEDED),
        ("3", CommandState.PENDING),
    ]
```

Add the matrix nodes for exact/nonmatching event, foreign session, retained ingress, unknown identity, disabled control, non-setting polls, invalid CRC, and audit identity across prepared/started/drained/unknown/failed outcomes.

- [ ] **Step 3: Write restart, retry-limit, and stream E2E tests**

Persist and compare parsed stable/attempt fields:

```python
async def test_e2e_restart_retries_with_stable_identity(
    harness: LocalControlHarness,
) -> None:
    first = await harness.begin_local_delivery("tbl_box_prms", "MODE", "2")
    first_tags = parse_tags(first.wire_frame)
    await harness.drop_box_before_ack()
    await harness.restart_proxy_and_store()
    second = await harness.begin_next_eligible_dialogue()
    second_tags = parse_tags(second.wire_frame)
    for tag in (
        "ID", "ID_Set", "DT", "ID_Device", "NewValue", "Confirm",
        "TblName", "TblItem", "ID_Server",
    ):
        assert second_tags[tag] == first_tags[tag]
    for tag in ("TSec", "ver", "CRC"):
        assert second_tags[tag] != first_tags[tag]
    assert second.attempt_number == first.attempt_number + 1


async def test_e2e_partial_and_coalesced_frames_preserve_bytes_and_order(
    harness: LocalControlHarness,
) -> None:
    frames = (harness.sensor_frame("A"), harness.isnewset_poll(), harness.sensor_frame("B"))
    joined = b"".join(frames)
    await harness.fake_box.send_chunks((joined[:17], joined[17:91], joined[91:]))
    assert await harness.fake_cloud.read_bytes(len(joined)) == joined
```

Add limit `1` and `8` with no ninth attempt, terminal NACK, duplicate event after restart, direct event while awaiting ACK, duplicate sessions, exact frame/capture/audit parity in ONLINE and OFFLINE, and buffer boundary/overflow connection closure.

Implement every traceability node with these exact pass oracles:

| E2E node | Setup and required assertions |
|---|---|
| `test_e2e_online_cloud_priority_then_local_batch` | Enqueue two targets; prove poll reaches cloud first, cloud Setting/ACK bytes round-trip unchanged, terminal END is withheld, two local Settings are sent only after END with local ACKs absent from cloud, and the original END returns byte-exact last. |
| `test_e2e_foreign_session_cannot_advance_active_command` | Open two BOX clients bound to the same device; session A owns attempt 1; send ACK/NACK from B; prove A's command/attempt/fingerprint are unchanged and B neither claims the successor nor receives A's deferred END. |
| `test_e2e_ack_is_delivery_only_until_exact_event` | ACK a drained local Setting; assert `awaiting_event`, zero confirmed MQTT publications, then send exact evidence and assert one publication after `confirmed` commits. |
| `test_e2e_matching_event_confirms_and_nonmatching_event_does_not` | Send four distinct valid events with wrong device, target, key, and value; assert permanent unmatched receipts and unchanged state, then send one exact event ID and assert one confirmation. |
| `test_e2e_rapid_same_key_updates_do_not_overwrite_attempted_command` | Send value 1, enqueue 2 then 3 before ACK; assert value 1 immutable, value 2 superseded, value 3 successor, and the ACK/event for 1 never publishes 3. |
| `test_e2e_restart_retries_with_stable_identity` | Close after durable attempt without ACK, restart with the same DB, reach a new eligible END, and compare every stable versus refreshed wire field plus original command/audit IDs and incremented attempt. |
| `test_e2e_retry_limit_and_terminal_nack` | With limits 1 and 8, force uncertain writes and prove no attempt above the configured/hard limit; in a fresh command send diagnostic NACK and prove terminal failed plus no automatic retry on later polls. |
| `test_e2e_invalid_crc_never_selects_advances_or_confirms` | Corrupt one poll, one local ACK, and one exact-looking event independently; prove zero claim for the poll, retry/fail and close for the proxy-owned ACK, and no confirmation/publication for the event. |
| `test_e2e_partial_and_coalesced_frames_preserve_bytes_and_order` | Split a terminator across reads and coalesce unrelated frames around `IsNewSet`/END substitution; compare complete bytes on both legs and prove each non-substituted frame appears exactly once in order. |
| `test_e2e_non_setting_polls_never_trigger_delivery` | With pending work, send `IsNewFW`, `IsNewWeather`, table upload, and unrelated ACK; assert no local Setting bytes and command remains pending. |
| `test_e2e_disabled_control_has_no_subscription_discovery_or_write` | Seed pending/retry/awaiting-ACK/awaiting-event rows, start disabled, assert recovery/deadline mappings, no control subscription/entity, all tombstones, no Setting writes, and passive exact confirmation only for the awaiting-event row. |
| `test_e2e_retained_control_never_enters_local_batch` | Emit retained JSON and compatibility messages before normal input; assert ingress-only rejection rows, no transitions/commands/proxy mode dispatch, then prove one non-retained exact message can enqueue. |
| `test_e2e_no_delivery_before_valid_device_identity` | Start without persisted identity, emit MQTT input and empty/invalid-CRC BOX identity frames, assert no handler/claim; send one CRC-valid identified frame with observed counters and assert exact subscriptions become eligible afterward. |
| `test_e2e_audit_identity_survives_all_write_outcomes` | Script prepared, started, drained, failed, unknown, retry, ACK, and event-confirmed steps; compare one command/audit ID, actual `ID`/`ID_Set`, exact frames, attempt numbers, capture links, and transition rows throughout. |

The shared cloud-priority node supplies SI-1 and SI-2 evidence. Each function must assert database state plus external bytes/publications; log text is never the pass oracle.

- [ ] **Step 4: Run E2E tests to verify red**

Run:

```bash
LOCAL_CONTROL_EGRESS_REPORT=reports/egress-guard.json .venv/bin/python -m pytest tests/v2/e2e -m e2e -vv
```

Expected: FAIL because the loopback harness/fakes and complete E2E nodes are not implemented.

- [ ] **Step 5: Implement the loopback harness under the guard**

Build `Config` with numeric loopback values for proxy, cloud, MQTT, DNS, and telemetry; set telemetry disabled; use port `0`; use temporary DB/capture paths; and call `EgressGuard.validate_config()` before startup. Inject every fake rather than relying on configuration to avoid production constructors. Wait for actual bound ports through explicit readiness events, never sleeps used as pass oracles.

Make `restart_proxy_and_store()` close connection/router/coordinator/store in production order, preserve DB/capture files, construct fresh objects, run recovery, and open a fresh BOX/cloud dialogue. Keep the same fake MQTT event history only where a test asserts duplicate publication; otherwise clear observed output explicitly.

Complete every Task 1 E2E node with exact store state, raw byte, capture link, audit identity, MQTT state, connection-close, and attempt-count assertions. No test passes solely from logs or elapsed time.

- [ ] **Step 6: Run E2E tests to verify green and inspect guard evidence**

Run:

```bash
LOCAL_CONTROL_EGRESS_REPORT=reports/egress-guard.json .venv/bin/python -m pytest tests/v2/e2e -m e2e -vv
.venv/bin/python -c 'import json; data=json.load(open("reports/egress-guard.json", encoding="utf-8")); assert data["status"] == "pass"; assert data["blocked_violation_count"] == 0'
```

Expected: every E2E node PASS; the report records all guard policies/probes, only numeric loopback allowances, zero unexpected violations, and pytest exit status `0`.

- [ ] **Step 7: Run the full suite with E2E included**

Run:

```bash
LOCAL_CONTROL_EGRESS_REPORT=reports/egress-guard.json .venv/bin/python -m pytest tests/v2 -q
```

Expected: PASS with no DNS, LAN, public, production MQTT, telemetry, cloud, or BOX transport attempt.

- [ ] **Step 8: Commit fake-endpoint E2E coverage**

```bash
git add tests/v2/egress_guard.py tests/v2/conftest.py tests/v2/e2e
git commit -m "test: cover local-setting transactions end to end"
```

---

### Task 15: Make Coverage, Type, Lint, and Security Gates Fail Closed

**Files:**
- Create: `ci/check_coverage.py`
- Create: `tests/v2/test_ci_coverage_gate.py`
- Create: `tests/v2/test_local_setting_security.py`
- Modify: `tests/v2/test_release_evidence.py`
- Modify: `.coveragerc`
- Modify: `ci/ci.sh`
- Modify: `.github/scripts/run_tests.sh`
- Modify: `.github/scripts/run_security.sh`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/pylint.yml`
- Modify: `.github/workflows/security-scan.yml`
- Modify: `.gitleaks.toml`
- Modify: `.semgrep.yml`

**Interfaces:**
- Consumes: Full Task 14 test suite and generated Cobertura XML.
- Produces: separate strict statement/branch result, blocking local/CI commands, scanner artifacts, and test-node existence evidence consumed by Task 16 and PR review.

- [ ] **Step 1: Write exact coverage-boundary tests**

Create `tests/v2/test_ci_coverage_gate.py` against a pure function:

```python
from decimal import Decimal
from pathlib import Path

import pytest

from ci.check_coverage import CoverageGateError, evaluate_coverage


@pytest.mark.parametrize(
    ("lines", "branches", "message"),
    [
        ((80, 100), (80, 100), "statement coverage"),
        ((81, 100), (80, 100), "branch coverage"),
        ((80, 100), (81, 100), "statement coverage"),
    ],
)
def test_exactly_eighty_in_either_metric_fails(
    tmp_path: Path,
    lines: tuple[int, int],
    branches: tuple[int, int],
    message: str,
) -> None:
    xml = write_cobertura(tmp_path, lines=lines, branches=branches)
    with pytest.raises(CoverageGateError, match=message):
        evaluate_coverage(xml, minimum=Decimal("80.0"))


def test_both_metrics_strictly_above_eighty_pass(tmp_path: Path) -> None:
    result = evaluate_coverage(
        write_cobertura(tmp_path, lines=(8001, 10000), branches=(4001, 5000)),
        minimum=Decimal("80.0"),
    )
    assert result.statement_percent == Decimal("80.0100")
    assert result.branch_percent == Decimal("80.0200")
    assert result.passed is True


def test_zero_branch_denominator_fails(tmp_path: Path) -> None:
    xml = write_cobertura(tmp_path, lines=(9, 10), branches=(0, 0))
    with pytest.raises(CoverageGateError, match="branch denominator is zero"):
        evaluate_coverage(xml, minimum=Decimal("80.0"))
```

Add malformed XML, missing attributes, zero line denominator, and deterministic JSON-key/order tests.

- [ ] **Step 2: Write evidence-node and local security contract tests**

Extend `test_release_evidence.py` to split `path::node`, require the path to exist, parse Python AST, and require every referenced test function to exist. Create `test_local_setting_security.py` with source/behavior checks:

```python
def test_store_uses_parameterized_sql_for_dynamic_values() -> None:
    tree = ast.parse(STORE.read_text(encoding="utf-8"))
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        if call.func.attr not in {"execute", "executemany"} or not call.args:
            continue
        query = call.args[0]
        assert not isinstance(query, ast.JoinedStr)
        assert not (isinstance(query, ast.BinOp) and isinstance(query.op, (ast.Add, ast.Mod)))


def test_production_has_no_raw_setting_replay_or_wildcard_device_subscription() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in PRODUCTION_PYTHON)
    assert "replay_setting_frame.xml" not in source
    assert 'oig/+/control/set' not in source
    assert '}/+/set/#' not in source


def test_ingress_logs_do_not_include_raw_payload_at_info(caplog: pytest.LogCaptureFixture) -> None:
    secret_marker = "CONTROL_SECRET_MARKER"
    exercise_rejected_ingress(secret_marker)
    assert secret_marker not in "\n".join(record.getMessage() for record in caplog.records)
```

Add default gate false, retain-before-decode, 1 MiB bounds, event deduplication, and XML escaping behavior checks.

- [ ] **Step 3: Run gate/security contract tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_ci_coverage_gate.py tests/v2/test_local_setting_security.py tests/v2/test_release_evidence.py -q
```

Expected: FAIL because the checker does not exist, CI remains at 69%, and the trace-node existence test is not implemented.

- [ ] **Step 4: Implement an independent Cobertura checker**

Create `ci/check_coverage.py` with no third-party imports:

```python
@dataclass(frozen=True, slots=True)
class CoverageGateResult:
    lines_covered: int
    lines_valid: int
    branches_covered: int
    branches_valid: int
    statement_percent: Decimal
    branch_percent: Decimal
    minimum_exclusive: Decimal
    passed: bool


def evaluate_coverage(path: Path, *, minimum: Decimal) -> CoverageGateResult:
    try:
        root = ElementTree.parse(path).getroot()
        lines_covered = int(root.attrib["lines-covered"])
        lines_valid = int(root.attrib["lines-valid"])
        branches_covered = int(root.attrib["branches-covered"])
        branches_valid = int(root.attrib["branches-valid"])
    except (OSError, ElementTree.ParseError, KeyError, ValueError) as exc:
        raise CoverageGateError(f"invalid coverage XML: {exc}") from exc
    if lines_valid == 0:
        raise CoverageGateError("statement denominator is zero")
    if branches_valid == 0:
        raise CoverageGateError("branch denominator is zero")
    statement = Decimal(lines_covered) * 100 / Decimal(lines_valid)
    branch = Decimal(branches_covered) * 100 / Decimal(branches_valid)
    if statement <= minimum:
        raise CoverageGateError(f"statement coverage {statement} is not greater than {minimum}")
    if branch <= minimum:
        raise CoverageGateError(f"branch coverage {branch} is not greater than {minimum}")
    return CoverageGateResult(
        lines_covered, lines_valid, branches_covered, branches_valid,
        statement, branch, minimum, True,
    )
```

The CLI takes XML path, `--minimum`, and `--output`; it writes deterministic JSON with raw counts, four-decimal percentages, exclusive threshold, and result on success or failure, then exits nonzero on any failure.

Set `.coveragerc`:

```ini
[run]
source = addon/oig-proxy
relative_files = True
branch = True

[report]
precision = 2
show_missing = True
fail_under = 80.01
```

- [ ] **Step 5: Make test, lint, type, and security scripts blocking**

Use these exact commands in local scripts and workflows, creating `reports/` first:

```bash
.venv/bin/python -m pytest tests/v2 \
  --junitxml=reports/junit.xml \
  --cov=addon/oig-proxy \
  --cov-branch \
  --cov-report=term-missing \
  --cov-report=xml:reports/coverage.xml \
  --cov-fail-under=80.01

.venv/bin/python ci/check_coverage.py reports/coverage.xml \
  --minimum 80.0 \
  --output reports/coverage-gate.json

.venv/bin/python -m mypy addon/oig-proxy --ignore-missing-imports
.venv/bin/python -m flake8 addon/oig-proxy tests/v2
PYTHONPATH=addon/oig-proxy .venv/bin/python -m pylint addon/oig-proxy tests/v2 \
  --output-format=json > reports/pylint.json

.venv/bin/python -m bandit -r addon/oig-proxy \
  -x addon/oig-proxy/tests \
  -ll -f json -o reports/bandit.json

.venv/bin/semgrep --config .semgrep.yml --error \
  --json --output reports/semgrep.json addon/oig-proxy

gitleaks detect --source . --config .gitleaks.toml \
  --no-banner --redact \
  --report-format json --report-path reports/gitleaks.json

.venv/bin/python -m safety check \
  -r addon/oig-proxy/requirements.txt \
  --output json > reports/safety.json
```

Remove `--exit-zero`, `|| true`, warning-only exits, deleted V1 security-test references, and `continue-on-error`. Scan production plus all `tests/v2` in lint/pylint. Keep the existing mypy workaround only if a focused test proves it is still required, restore the moved file through a shell trap, and propagate the real exit code.

- [ ] **Step 6: Harden scanner configuration and artifact upload**

Remove `.gitleaks.toml` exclusions matching every `test_*.py` or `local_*.py`; retain only cache, tool, generated-report, and known binary exclusions. Add four ERROR-level Python Semgrep rules for an f-string, `%`, `+`, or `.format()` expression passed as the SQL argument to SQLite `execute`/`executemany`; parameterized literal queries remain accepted.

In GitHub Actions, run the same repository `.semgrep.yml`, Gitleaks without success override, Bandit medium/high blocking, and Safety blocking. Upload `junit.xml`, coverage XML/gate, egress report, Bandit/Semgrep/Gitleaks/Safety/Pylint reports, traceability, and OWASP report with `if: always()`. Absence of a required report fails a separate evidence step.

- [ ] **Step 7: Run gate/security contract tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_ci_coverage_gate.py tests/v2/test_local_setting_security.py tests/v2/test_release_evidence.py -q
```

Expected: PASS for exclusive 80% boundaries, test-node existence, parameterized SQL, replay/wildcard removal, and bounded logging.

- [ ] **Step 8: Run the coverage gate once and close uncovered branches**

Run:

```bash
LOCAL_CONTROL_EGRESS_REPORT=reports/egress-guard.json .venv/bin/python -m pytest tests/v2 --junitxml=reports/junit.xml --cov=addon/oig-proxy --cov-branch --cov-report=term-missing --cov-report=xml:reports/coverage.xml --cov-fail-under=80.01
.venv/bin/python ci/check_coverage.py reports/coverage.xml --minimum 80.0 --output reports/coverage-gate.json
```

Expected: both commands PASS; add focused tests in the owning Task 4–13 test module for every uncovered changed branch until statement and branch percentages are each greater than `80.0`.

- [ ] **Step 9: Commit closed verification gates**

```bash
git add ci/check_coverage.py ci/ci.sh tests/v2/test_ci_coverage_gate.py tests/v2/test_local_setting_security.py tests/v2/test_release_evidence.py .coveragerc .github/scripts/run_tests.sh .github/scripts/run_security.sh .github/workflows/ci.yml .github/workflows/pylint.yml .github/workflows/security-scan.yml .gitleaks.toml .semgrep.yml
git commit -m "ci: enforce local-setting evidence gates"
```

---

### Task 16: Document and Version the 2.2.0 Transaction Contract

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `SECURITY.md`
- Modify: `docs/v2/configuration.md`
- Rewrite: `docs/v2/twin.md`
- Modify: `docs/v2/architecture.md`
- Modify: `docs/v2/proxy_modes.md`
- Modify: `docs/CI_CD_OVERVIEW.md`
- Modify: `docs/SECURITY_TESTING.md`
- Modify: `.github/workflows/README.md`
- Modify: `tests/v2/test_release_evidence.py`
- Modify: `tests/v2/test_addon_dns_config.py`

**Interfaces:**
- Consumes: Shipped behavior and gate commands from Tasks 3 through 15.
- Produces: operator-facing 2.2.0 contract, rollback/residual-risk guidance, and release assertions consumed by Task 17 and the PR.

- [ ] **Step 1: Write release/document contract tests**

Extend `test_release_evidence.py`:

```python
def test_release_version_and_changelog_are_2_2_0() -> None:
    addon = json.loads((ROOT / "addon/oig-proxy/config.json").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert addon["version"] == "2.2.0"
    assert "## [2.2.0] - 2026-08-06" in changelog
    assert "## [2.1.1] - 2026-06-27" in changelog


def test_twin_docs_state_ack_event_and_residual_limit_are_explicit() -> None:
    text = (ROOT / "docs/v2/twin.md").read_text(encoding="utf-8")
    for required in (
        "/data/twin_queue.db", "awaiting_ack", "awaiting_event",
        "ACK/Setting is delivery evidence", "tbl_events",
        "ID_Server=9", "control_max_attempts", "retained",
        "sequential ACK/NACK protocol limitation",
    ):
        assert required in text


def test_configuration_docs_match_control_defaults_and_precedence() -> None:
    text = (ROOT / "docs/v2/configuration.md").read_text(encoding="utf-8")
    assert "30 add-on parameters" in text
    for row in (
        "`control_mqtt_enabled` | `CONTROL_MQTT_ENABLED` | `false`",
        "`control_ack_timeout_s` | `CONTROL_ACK_TIMEOUT_S` | `30`",
        "`control_event_timeout_s` | `CONTROL_EVENT_TIMEOUT_S` | `300`",
        "`control_command_ttl_s` | `CONTROL_COMMAND_TTL_S` | `900`",
        "`control_max_attempts` | `CONTROL_MAX_ATTEMPTS` | `8`",
    ):
        assert row in text
    assert "CONTROL_ACK_TIMEOUT_S has precedence" in text
```

Add assertions for disabled restart semantics, DB preservation/rollback, cloud-first ONLINE, exactly-one OFFLINE, no replay path, coverage/security commands, support policy, advisory URL, and no claim that ACK confirms execution.

- [ ] **Step 2: Run release/document tests to verify red**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_release_evidence.py tests/v2/test_addon_dns_config.py -q
```

Expected: FAIL because changelog/docs still describe the in-memory queue, ACK confirmation, replay behavior, and 69% coverage.

- [ ] **Step 3: Write the changelog and operator configuration contract**

Insert:

```markdown
## [2.2.0] - 2026-08-06

### Security
- Bound local control to an exact learned device, non-retained MQTT input, CRC-valid BOX evidence, UUID connection ownership, allowlisted Decimal constraints, and XML-safe serialization.
- Fail local control closed on store, lock, migration, render, write-outcome, session, direction, deadline, or correlation failure while preserving transparent cloud forwarding where the dialogue is not proxy-owned.

### Added
- Durable `/data/twin_queue.db` transaction, attempt, transition, ingress, and event-receipt history with restart recovery.
- Cloud-first ONLINE/HYBRID substitution, exactly-once OFFLINE decisions, stable bounded retry identity, and exact execution-event confirmation.
- Hermetic fake-endpoint E2E, SI-1 through SI-15 traceability, statement/branch coverage gates, and blocking security evidence.

### Changed
- `ACK/Setting` now means delivered/pending execution; only an exact `tbl_events`, `Type=Setting` event publishes confirmed state.
- Local Setting retries preserve stable fields and refresh only `TSec`, `ver`, and CRC, with a shipped and hard maximum of eight attempts.

### Removed
- Process-memory overwrite/delete delivery, wildcard-device control topics, pre-cloud setting injection, firmware/weather triggers, ACK-based state publication, duplicate serializer paths, and `/data/replay_setting_frame.xml` injection.
```

Backfill `2.1.1` dated `2026-06-27` from commit `1589c73`. In configuration docs, list all 30 add-on options and exact environment names/defaults; document restart sampling, internal DB/cloud-cycle paths, 2.2.x legacy `CLOUD_ACK_TIMEOUT` precedence table/deprecation, bounds, fail-closed behavior, and no event-timeout alias.

- [ ] **Step 4: Replace obsolete twin/proxy/architecture documentation**

Write `docs/v2/twin.md` with these exact sections: scope and disabled default; exact-device MQTT topics; retained/input/Decimal/XML validation; durable schema summary; state machine and terminal states; same-target ordering; wire field table/order; ONLINE/HYBRID cloud-first sequence; OFFLINE one-response table; ACK/NACK correlation; exact event confirmation/deduplication; retry/restart/deadlines; audit/capture/state publication; database failure/rollback; security boundaries; residual sequential ACK/NACK protocol limitation.

State verbatim that captured ACKs lack command-side `ID` and `ID_Set`; multi-command batches therefore depend on strict half-duplex ordering, exact fingerprint deduplication, session ownership, and Rdt ordering, and a novel regenerated duplicate remains indistinguishable. State that eliminating this residual risk requires one local Setting per `IsNewSet` cycle.

Update architecture with Store -> Coordinator -> SettingDialog -> SerializedBoxWriter ownership and store recovery before subscriptions. Update proxy modes with cloud-first correlated END substitution and OFFLINE Setting-or-END tables. Document that 2.1.1 rollback ignores and preserves the schema-v1 database, and that 2.2.0 never downgrades or recreates it.

- [ ] **Step 5: Update security, CI, and support documentation**

Set `SECURITY.md` support to `2.2.x` and mark `<=2.1.x` unsupported for hardened local control. Use private advisory reporting at `https://github.com/Muriel2Horak/oig-proxy/security/advisories/new`; remove placeholder reporting text.

Replace every documented 69%/statement-only command with Task 15's exact full command and separate checker. Document all uploaded artifacts and blocking definitions. Update security testing to the five blocking tools, repository Semgrep config, medium/high Bandit rule, zero unresolved vulnerabilities/secrets, OWASP rows, and loopback egress evidence. Do not document manual BOX, live MQTT, HA deployment, or production cloud validation as part of this release task.

- [ ] **Step 6: Run release/document tests to verify green**

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_release_evidence.py tests/v2/test_addon_dns_config.py -q
```

Expected: PASS for version, option count/defaults, state semantics, residual risk, rollback, security reporting, and gate commands.

- [ ] **Step 7: Check documentation for stale unsafe claims**

Run:

```bash
rg -n -i '69%|overwrite.*same key|ack.*confirm|replay_setting_frame|oig/\+/control/set|no persistence' README.md SECURITY.md CHANGELOG.md docs .github/workflows/README.md
```

Expected: no stale assertion that local control overwrites sent work, confirms on ACK, uses wildcard device topics, lacks persistence, exposes replay injection, or accepts a 69% gate. Historical/design descriptions may appear only when explicitly labeled as removed behavior.

- [ ] **Step 8: Commit release documentation**

```bash
git add CHANGELOG.md README.md SECURITY.md docs/v2/configuration.md docs/v2/twin.md docs/v2/architecture.md docs/v2/proxy_modes.md docs/CI_CD_OVERVIEW.md docs/SECURITY_TESTING.md .github/workflows/README.md tests/v2/test_release_evidence.py tests/v2/test_addon_dns_config.py
git commit -m "docs: document local-setting transactions for 2.2.0"
```

---

### Task 17: Verify, Record OWASP Evidence, and Open the PR

**Files:**
- Create: `docs/superpowers/reports/2026-08-06-local-setting-transaction-hardening-owasp.md`
- Modify: `tests/v2/test_release_evidence.py`
- Generated, ignored: `reports/junit.xml`
- Generated, ignored: `reports/coverage.xml`
- Generated, ignored: `reports/coverage-gate.json`
- Generated, ignored: `reports/egress-guard.json`
- Generated, ignored: `reports/bandit.json`
- Generated, ignored: `reports/semgrep.json`
- Generated, ignored: `reports/gitleaks.json`
- Generated, ignored: `reports/safety.json`
- Generated, ignored: `reports/pylint.json`
- Generated, ignored: `reports/pr-body.md`

**Interfaces:**
- Consumes: Every implementation, test, documentation, and gate from Tasks 1 through 16.
- Produces: passing reproducible evidence, completed OWASP review, one final evidence commit, and a checked GitHub pull request through `gh-muriel`.

- [ ] **Step 1: Invoke completion verification and confirm safe repository scope**

Use `superpowers:verification-before-completion` before making any completion claim. Run:

```bash
git status --short --branch
git remote get-url origin
git log --oneline origin/main..HEAD
```

Expected: branch is `codex/local-setting-transaction-hardening`; origin is `git@github.com:Muriel2Horak/oig-proxy.git`; `.omx/` and `output/` remain untracked/unstaged; no `.env`, database, capture, credential, or generated report is staged.

- [ ] **Step 2: Run full unit, integration, E2E, MNP/smoke, and coverage verification**

Run from repository root:

```bash
mkdir -p reports
LOCAL_CONTROL_EGRESS_REPORT=reports/egress-guard.json .venv/bin/python -m pytest tests/v2 \
  --junitxml=reports/junit.xml \
  --cov=addon/oig-proxy \
  --cov-branch \
  --cov-report=term-missing \
  --cov-report=xml:reports/coverage.xml \
  --cov-fail-under=80.01

.venv/bin/python ci/check_coverage.py reports/coverage.xml \
  --minimum 80.0 \
  --output reports/coverage-gate.json

LOCAL_CONTROL_EGRESS_REPORT=reports/egress-guard.json .venv/bin/python -m pytest \
  tests/v2/e2e/test_local_setting_transaction.py::test_e2e_online_cloud_priority_then_local_batch \
  tests/v2/e2e/test_local_setting_transaction.py::test_e2e_restart_retries_with_stable_identity \
  -m e2e -vv
```

Expected: all tests PASS; statement and branch percentages are each strictly greater than `80.0`; both MNP/smoke nodes PASS; egress status is `pass`, all self-probes pass, unexpected violation count is zero, and every allowed connection is numeric loopback.

- [ ] **Step 3: Run type, lint, and security verification**

Run every command and stop on the first failure:

```bash
.venv/bin/python -m mypy addon/oig-proxy --ignore-missing-imports
.venv/bin/python -m flake8 addon/oig-proxy tests/v2
PYTHONPATH=addon/oig-proxy .venv/bin/python -m pylint addon/oig-proxy tests/v2 --output-format=json > reports/pylint.json

.venv/bin/python -m bandit -r addon/oig-proxy \
  -x addon/oig-proxy/tests \
  -ll -f json -o reports/bandit.json

.venv/bin/semgrep --config .semgrep.yml --error \
  --json --output reports/semgrep.json addon/oig-proxy

gitleaks detect --source . --config .gitleaks.toml \
  --no-banner --redact \
  --report-format json --report-path reports/gitleaks.json

.venv/bin/python -m safety check \
  -r addon/oig-proxy/requirements.txt \
  --output json > reports/safety.json

git diff --check
git diff --check origin/main...
```

Expected: every command exits `0`; no Gitleaks secret, Bandit medium/high finding, Semgrep warning/error, dependency vulnerability, mypy error, flake8 error, pylint error, or whitespace error exists. Fix the owning production branch and add a regression test before rerunning the entire Step 2 and Step 3 sequence; do not suppress a functional defect.

- [ ] **Step 4: Write completed OWASP evidence only from passing results**

Create `docs/superpowers/reports/2026-08-06-local-setting-transaction-hardening-owasp.md` only after Steps 2 and 3 pass. Include command timestamps, exact statement/branch percentages from `coverage-gate.json`, egress report status, scanner artifact hashes, and these seven PASS rows:

```markdown
| Risk class | Implemented control | Source symbols | Unit evidence | Integration evidence | E2E evidence | Scanner evidence | Result |
|---|---|---|---|---|---|---|---|
| Input injection | Decimal allowlist/range/step/finite validation, strict UTF-8/JSON, XML 1.0 escaping, parameterized SQLite | `settings_constraints.py`, `twin/handler.py`, `protocol/frames.py`, `twin/store.py` | `test_settings_constraints.py`, `test_local_setting_security.py` | `test_twin_handler.py` | `test_e2e_retained_control_never_enters_local_batch` | Bandit, Semgrep | PASS |
| Authorization and device binding | Exact learned device topic, CRC-valid connection binding, UUID session CAS | `twin/handler.py`, `proxy/server.py`, `twin/store.py` | SI-3, SI-14 unit nodes | SI-3, SI-14 integration nodes | SI-3, SI-14 E2E nodes | Semgrep | PASS |
| Replay | Retained MQTT rejection, ACK fingerprint dedupe, durable event receipts, replay-file removal | `twin/handler.py`, `twin/ack_parser.py`, `twin/store.py`, `proxy/server.py` | SI-9, SI-13 unit nodes | SI-9, SI-13 integration nodes | SI-9, SI-13 E2E nodes | Gitleaks, Semgrep | PASS |
| Resource exhaustion | 16 KiB ingress cap, 1 MiB stream/held caps, one active attempt/device, bounded retries/deadlines | `twin/handler.py`, `protocol/frame.py`, `proxy/dialog.py`, `twin/store.py` | stream/attempt boundary nodes | `test_setting_streams.py` | partial/coalesced and retry-limit E2E nodes | Bandit | PASS |
| Sensitive logging | Bounded/redacted ingress and errors, no INFO raw payload, redacted secret scan output | `twin/handler.py`, `telemetry/settings_audit.py` | `test_ingress_logs_do_not_include_raw_payload_at_info` | audit contract nodes | audit identity E2E node | Gitleaks | PASS |
| Insecure defaults | Control disabled, no unknown-device subscription, max eight, FULL/WAL/lock, exact discovery tombstones | `config.py`, `main.py`, `mqtt/client.py`, `twin/store.py` | SI-8, SI-12, SI-14 unit nodes | corresponding integration nodes | corresponding E2E nodes | Bandit, Safety | PASS |
| Fail-open paths | Durable prepare before write; store/CRC/session/write failures prohibit local mutation and preserve transparent unowned cloud flow | `twin/delivery.py`, `proxy/writer.py`, `proxy/server.py`, `main.py` | write/store failure nodes | online/offline failure nodes | writer/restart/disabled E2E nodes | all scanner artifacts | PASS |
```

Do not write a PASS row unless its cited tests and scanners passed in the current checkout. A repository-owner risk acceptance must name the finding, scope, expiry, and exact PR review URL; without it, leave the task incomplete and fix the issue.

- [ ] **Step 5: Add and run OWASP report contract tests**

Add:

```python
OWASP = ROOT / "docs/superpowers/reports/2026-08-06-local-setting-transaction-hardening-owasp.md"


def test_owasp_review_covers_required_risk_classes() -> None:
    text = OWASP.read_text(encoding="utf-8")
    for risk in (
        "Input injection", "Authorization and device binding", "Replay",
        "Resource exhaustion", "Sensitive logging", "Insecure defaults",
        "Fail-open paths",
    ):
        assert f"| {risk} |" in text


def test_owasp_review_has_no_unresolved_result() -> None:
    text = OWASP.read_text(encoding="utf-8")
    result_cells = re.findall(r"\| (PASS|RISK_ACCEPTED) \|$", text, re.MULTILINE)
    assert len(result_cells) == 7
```

Run:

```bash
.venv/bin/python -m pytest tests/v2/test_release_evidence.py -q
```

Expected: PASS for traceability, node existence, release/docs, seven OWASP classes, and seven resolved results.

- [ ] **Step 6: Commit final verification evidence**

```bash
git add docs/superpowers/reports/2026-08-06-local-setting-transaction-hardening-owasp.md tests/v2/test_release_evidence.py
git commit -m "docs: record local-setting security verification"
```

Run Steps 2 and 3 once more after this commit because the committed report/test changes alter the checkout.

- [ ] **Step 7: Request a fresh code review before remote publication**

Invoke `superpowers:requesting-code-review`. Require findings-first review against the approved design, SI matrix, store transactions, connection routing, enabled/disabled lifecycle, E2E egress evidence, and every gate output. Resolve each blocking finding with TDD and repeat Steps 2 through 6; record no approval until the reviewer sees the final commit.

- [ ] **Step 8: Prepare an exact PR body and verify the outgoing diff**

Create ignored `reports/pr-body.md` with:

```markdown
## Summary
- Replaces local-setting memory/global state with device-bound SQLite transactions and explicit attempt outcomes.
- Makes ONLINE/HYBRID cloud-first and OFFLINE exactly once while preserving raw byte order.
- Separates ACK delivery from exact event confirmation and adds bounded stable-identity retry/recovery.

## Safety evidence
- Design: `docs/superpowers/specs/2026-08-06-local-setting-transaction-hardening-design.md`
- SI matrix: `docs/superpowers/reports/2026-08-06-local-setting-transaction-hardening-traceability.md`
- OWASP review: `docs/superpowers/reports/2026-08-06-local-setting-transaction-hardening-owasp.md`
- Uploaded reports: JUnit, coverage XML/gate, egress guard, Pylint, Bandit, Semgrep, Gitleaks, Safety.

## Verification
- Full `tests/v2`, local fake-endpoint E2E, MNP/smoke, mypy, flake8, pylint, Bandit, Semgrep, Gitleaks, Safety, and both diff checks passed.
- Statement and branch coverage are each strictly greater than 80.0%.
- Egress guard passed with zero unexpected violations; tests used numeric loopback transports only.

## Residual protocol limitation
Captured ACK/NACK frames contain no command-side `ID` or `ID_Set`. Multi-command local batches therefore rely on strict half-duplex ordering, UUID session ownership, exact fingerprint deduplication, and Rdt ordering; a newly regenerated duplicate response is indistinguishable from the next response.

## Operational boundary
No live BOX command, production cloud/MQTT/telemetry connection, Home Assistant deployment, or control enablement was performed.
```

Run:

```bash
git status --short --branch
git diff --stat origin/main...
git diff --check origin/main...
git log --oneline origin/main..HEAD
git remote get-url origin
```

Expected: only intended tracked changes/commits are outgoing; `.omx/`, `output/`, and `reports/` are unstaged; origin selects `gh-muriel`.

- [ ] **Step 9: Push the branch and create the PR**

```bash
git push -u origin codex/local-setting-transaction-hardening

gh-muriel pr create \
  --base main \
  --head codex/local-setting-transaction-hardening \
  --title "Harden local setting transactions for 2.2.0" \
  --body-file reports/pr-body.md
```

Do not deploy, enable live control, or merge as part of this task.

- [ ] **Step 10: Read back and require all PR checks**

```bash
LOCAL_SETTING_PR_NUMBER=$(gh-muriel pr view --json number --jq .number)
gh-muriel pr status
gh-muriel pr checks "$LOCAL_SETTING_PR_NUMBER" --watch
```

Expected: PR targets `main`, contains the final branch commit, and every required test, lint, type, coverage, security, and artifact check passes. Any pending/failed/missing check keeps the implementation incomplete; inspect, fix locally with a regression test, repeat verification, push, and re-read checks.

---

## Execution Dependency Graph

```text
Task 1 traceability
├── Task 2 hermetic egress
├── Task 3 configuration
├── Task 4 values/serializer
├── Task 5 frame/evidence validation
└── Task 6 locked schema
       └── Task 7 lifecycle transactions
              └── Task 8 coordinator/audit
                     ├── Task 9 dialogue/writer/capture
                     │      └── Task 10 ONLINE/HYBRID
                     │             └── Task 11 OFFLINE
                     └── Task 12 MQTT ingress
                                    └── Task 13 runtime/discovery/state
                                           └── Task 14 loopback E2E
                                                  └── Task 15 CI/security gates
                                                         └── Task 16 docs/version
                                                                └── Task 17 evidence/PR
```

Tasks 3, 4, 5, and the schema-only portion of Task 6 can be implemented in parallel worktrees only if their commits are reviewed and integrated in the dependency order above. Tasks 7 through 17 share contracts or repository-wide verification state and run sequentially.
