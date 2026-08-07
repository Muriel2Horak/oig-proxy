"""Validate the committed local-setting traceability evidence."""

import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
TRACE = (
    ROOT
    / "docs/superpowers/reports/"
    "2026-08-06-local-setting-transaction-hardening-traceability.md"
)
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
    """Require one matrix row for every approved safety invariant."""
    assert set(_trace_rows()) == set(range(1, 16))


def test_traceability_uses_unit_integration_and_e2e_nodes() -> None:
    """Require each row to name the required test-layer nodes."""
    for unit, integration, e2e in _trace_rows().values():
        assert unit.startswith("tests/v2/")
        assert integration.startswith("tests/v2/")
        assert e2e.startswith("tests/v2/e2e/")
        assert "::test_" in unit
        assert "::test_" in integration
        assert "::test_e2e_" in e2e


def test_every_traceability_node_exists_as_a_python_test_function() -> None:
    """Reject stale evidence paths and renamed or deleted test nodes."""
    for row in _trace_rows().values():
        for reference in row:
            relative_path, node = reference.split("::", maxsplit=1)
            path = ROOT / relative_path
            assert path.is_file(), reference
            functions = {
                item.name
                for item in ast.walk(
                    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                )
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert node.split("[", maxsplit=1)[0] in functions, reference


def test_release_version_and_changelog_are_2_2_0() -> None:
    addon = json.loads(
        (ROOT / "addon/oig-proxy/config.json").read_text(encoding="utf-8")
    )
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert addon["version"] == "2.2.0"
    assert "## [2.2.0] - 2026-08-06" in changelog
    assert "## [2.1.1] - 2026-06-27" in changelog


def test_twin_docs_state_ack_event_and_residual_limit_are_explicit() -> None:
    text = (ROOT / "docs/v2/twin.md").read_text(encoding="utf-8")
    for required in (
        "/data/twin_queue.db",
        "awaiting_ack",
        "awaiting_event",
        "ACK/Setting is delivery evidence",
        "tbl_events",
        "ID_Server=9",
        "control_max_attempts",
        "retained",
        "sequential ACK/NACK protocol limitation",
        "captured ACKs lack command-side `ID` and `ID_Set`",
        "one local Setting per `IsNewSet` cycle",
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
    assert "no event-timeout alias" in text


def test_release_docs_define_runtime_rollback_and_support_contracts() -> None:
    corpus = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "README.md",
            "SECURITY.md",
            "docs/v2/architecture.md",
            "docs/v2/proxy_modes.md",
            "docs/v2/twin.md",
        )
    )
    for required in (
        "cloud-first",
        "exactly one",
        "disabled restart",
        "preserves `/data/twin_queue.db`",
        "never downgrades or recreates",
        "2.1.1 rollback",
        "no replay path",
        "2.2.x",
        "https://github.com/Muriel2Horak/oig-proxy/security/advisories/new",
    ):
        assert required in corpus
    assert "ACK confirms execution" not in corpus


def test_ci_and_security_docs_publish_blocking_release_commands() -> None:
    corpus = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "docs/CI_CD_OVERVIEW.md",
            "docs/SECURITY_TESTING.md",
            ".github/workflows/README.md",
        )
    )
    for required in (
        "--cov-branch",
        "ci/check_coverage.py",
        "--minimum 80.0",
        "bandit.json",
        "semgrep.json",
        "gitleaks.json",
        "safety.json",
        "egress-guard.json",
        "local-setting-transaction-hardening-owasp.md",
    ):
        assert required in corpus
    assert "69%" not in corpus


def test_legacy_docs_do_not_restore_removed_local_control_contracts() -> None:
    corpus = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "docs/twin_architecture.md",
            "docs/event-driven-updates.md",
            "docs/protocol_behavior_specification.md",
            "docs/telemetry_overview.md",
        )
    )
    for removed in (
        "Twin._queue",
        "auto-queue SA",
        "oig_local/+/+/set",
        "No retry mechanism in proxy",
        "confirmed via cloud reason=Setting",
    ):
        assert removed not in corpus
    assert "transport ACK observed; delivery only, not execution" in corpus
    assert (
        "eliminating the residual ambiguity requires one local Setting"
        in corpus.replace("\n", " ")
    )
